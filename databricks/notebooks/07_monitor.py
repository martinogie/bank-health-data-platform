# Databricks notebook source
# MAGIC %md
# MAGIC # 07_monitor — score history, drift and backtest (M-08)
# MAGIC
# MAGIC Runs after `05_score` in the weekly job. Never changes a score.
# MAGIC
# MAGIC 1. **`ml.risk_score_history`** (append-only): this run's scores are added for keys
# MAGIC    (cert, report_date, model_version) not already there; rows are never updated or deleted.
# MAGIC    `source` = `weekly_run`. With `rescore_history = true` (a one-off), every earlier quarter
# MAGIC    is scored with the champion and added as `rescored`; quarters up to 2019 are marked
# MAGIC    `in_sample` (v5 was trained on 2001–2019).
# MAGIC 2. **PSI** for the score and each model feature, latest quarter vs the previous 4 quarters
# MAGIC    and vs 2001–2019 training (`_monitoring`).
# MAGIC 3. **Backtest**: realised PR-AUC and top-10% capture per quarter from 2020 whose outcome is
# MAGIC    known, from the stored scores.
# MAGIC
# MAGIC The exit summary carries all three; Airflow's `check_model` writes it to
# MAGIC `OPS.MODEL_MONITORING` and alerts (once alerts are switched on).

# COMMAND ----------

# MAGIC %pip install --quiet "mlflow==2.22.5" "scikit-learn==1.5.2"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

# MAGIC %run ./_features

# COMMAND ----------

# MAGIC %run ./_scoring_rules

# COMMAND ----------

# MAGIC %run ./_monitoring

# COMMAND ----------

import mlflow
from delta.tables import DeltaTable
from mlflow.tracking import MlflowClient
from sklearn.metrics import average_precision_score

NOTEBOOK = "07_monitor"
require_pinned_environment()
mlflow.set_registry_uri("databricks-uc")
dbutils.widgets.text("rescore_history", "false", "true: also score every earlier quarter (one-off)")
RESCORE = dbutils.widgets.get("rescore_history").strip().lower() == "true"
# Dev checks only: read gold from another catalog (prod's bank_health, READ-ONLY), because dev's gold
# holds only a few quarters. Writes (the history table) stay in dev.
dbutils.widgets.text("gold_catalog", "", "Read gold from this catalog (dev checks only)")
GOLD_CATALOG = dbutils.widgets.get("gold_catalog").strip()
if GOLD_CATALOG:
    if IS_PROD:
        raise ValueError("gold_catalog can only be changed in dev")
    GOLD = f"{GOLD_CATALOG}.gold"  # build_model_frame reads {GOLD}.bank_quarter

mv = MlflowClient().get_model_version_by_alias(CHAMPION_MODEL, "champion")
model = mlflow.sklearn.load_model(f"models:/{CHAMPION_MODEL}@champion")
features = list(model.feature_names_in_)
VERSION = int(mv.version)
HISTORY = f"{ML}.risk_score_history"

spark.sql(
    f"""CREATE TABLE IF NOT EXISTS {HISTORY} (
        cert INT, report_date DATE, model_name STRING, model_version INT,
        distress_probability DOUBLE, risk_band STRING, source STRING, in_sample BOOLEAN,
        added_at TIMESTAMP)
    COMMENT 'Append-only: every score the pipeline produced (and the v5 rescoring of earlier quarters). Never updated.'"""
)


def add_to_history(df):
    """Insert rows whose (cert, report_date, model_version) isn't there yet; never update."""
    before = spark.table(HISTORY).count()
    (
        DeltaTable.forName(spark, HISTORY)
        .alias("t")
        .merge(
            df.alias("s"),
            "t.cert = s.cert AND t.report_date = s.report_date AND t.model_version = s.model_version",
        )
        .whenNotMatchedInsertAll()
        .execute()
    )
    return spark.table(HISTORY).count() - before


scores = spark.table(f"{ML}.risk_scores")
weekly = scores.filter(F.col("distress_probability").isNotNull()).select(
    F.col("cert").cast("int").alias("cert"),
    "report_date",
    "model_name",
    F.col("model_version").cast("int").alias("model_version"),
    "distress_probability",
    "risk_band",
    F.lit("weekly_run").alias("source"),
    (F.col("report_date") <= F.lit(IN_SAMPLE_UNTIL)).alias("in_sample"),
    F.current_timestamp().alias("added_at"),
)
added = {"weekly_run": add_to_history(weekly)}

# COMMAND ----------

frame = build_model_frame()
latest = frame.agg(F.max("report_date")).first()[0]
cols = [
    "cert",
    "report_date",
    "is_foreign_office",
    "is_new_bank",
    "is_distressed",
    "is_model_row",
    "distress_next_4q",
    *features,
]
pdf = frame.select(*cols).filter(~F.col("is_foreign_office") & ~F.col("is_new_bank")).toPandas()
pdf["report_date"] = pd.to_datetime(pdf["report_date"]).dt.date
pdf["score"] = model.predict_proba(prepare_features(pdf, features))[:, 1]

if RESCORE:
    earlier = pdf[pdf["report_date"] < latest]
    rescored = spark.createDataFrame(
        earlier.assign(
            model_name=CHAMPION_MODEL,
            model_version=VERSION,
            distress_probability=earlier["score"],
            risk_band=None,
            source="rescored",
            in_sample=earlier["report_date"] <= pd.Timestamp(IN_SAMPLE_UNTIL).date(),
        )[
            [
                "cert",
                "report_date",
                "model_name",
                "model_version",
                "distress_probability",
                "risk_band",
                "source",
                "in_sample",
            ]
        ],
        schema="cert int, report_date date, model_name string, model_version int, distress_probability double, "
        "risk_band string, source string, in_sample boolean",
    ).withColumn("added_at", F.current_timestamp())
    added["rescored"] = add_to_history(rescored)
print("added to history:", added)

# COMMAND ----------

# Drift: the latest quarter's scored banks vs the previous 4 quarters and vs 2001-2019 training.
quarters = sorted(pdf["report_date"].unique())
prev4 = set(quarters[-5:-1])
current = pdf[(pdf["report_date"] == latest) & ~pdf["is_distressed"]]
references = {
    "previous_4q": pdf[pdf["report_date"].isin(prev4) & ~pdf["is_distressed"]],
    "training_2001_2019": pdf[
        (pdf["report_date"] <= pd.Timestamp(IN_SAMPLE_UNTIL).date()) & pdf["is_model_row"]
    ],
}
psi_rows = []
for ref_name, ref in references.items():
    for name in ["score", *features]:
        value = psi(ref[name].to_numpy(dtype="float64"), current[name].to_numpy(dtype="float64"))
        psi_rows.append(
            {
                "reference": ref_name,
                "name": name,
                "psi": None if value is None else round(value, 4),
                "level": psi_level(value),
            }
        )

# Backtest: stored scores (weekly or rescored, the champion's version) vs realised outcomes.
hist = (
    spark.table(HISTORY)
    .filter((F.col("model_version") == VERSION) & (F.col("report_date") >= BACKTEST_FROM))
    .select("cert", "report_date", "distress_probability")
    .toPandas()
)
hist["report_date"] = pd.to_datetime(hist["report_date"]).dt.date
outcomes = pdf[pdf["is_model_row"]][["cert", "report_date", "distress_next_4q"]]
bt = hist.merge(outcomes, on=["cert", "report_date"])
backtest_rows = [
    backtest_row(q, g["distress_next_4q"], g["distress_probability"], average_precision_score)
    for q, g in bt.groupby("report_date")
]
would_alert = alerts(psi_rows, backtest_rows)

summary = {
    "latest_quarter": str(latest),
    "model": f"{CHAMPION_MODEL} v{VERSION}",
    "history": {"added": added, "rows": spark.table(HISTORY).count()},
    "psi": psi_rows,
    "backtest": backtest_rows,
    "alerts": would_alert,
}
print(json.dumps({k: summary[k] for k in ("latest_quarter", "history", "alerts")}, indent=1))
print(
    "largest shifts vs the previous 4 quarters:",
    sorted(
        (r for r in psi_rows if r["reference"] == "previous_4q" and r["psi"] is not None),
        key=lambda r: -r["psi"],
    )[:5],
)
dbutils.notebook.exit(json.dumps(summary, default=str))
