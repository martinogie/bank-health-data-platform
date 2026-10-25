# Databricks notebook source
# MAGIC %md
# MAGIC # 04b_compare_cblr_variants — how to treat capital ratios CBLR banks don't report
# MAGIC
# MAGIC From 2020, banks using the Community Bank Leverage Ratio (CBLR) framework file no
# MAGIC risk-based capital ratio; FDIC data shows 0. Two fixes, compared on the **same split** as
# MAGIC model v4 (train 2001–2019, test 2020–2024), same `HistGradientBoostingClassifier` settings:
# MAGIC
# MAGIC - **(a) drop**: remove total risk-based capital from the features; keep Tier 1 leverage
# MAGIC   (reported by every CBLR bank).
# MAGIC - **(b) NaN + flag**: keep it, NULL for CBLR banks (done in gold), and add a `cblr_reporter` flag.
# MAGIC
# MAGIC **Result (2026-10-25)**: test PR-AUC (a) 0.356, (b) 0.357, v4 0.357; ROC-AUC 0.854 for all.
# MAGIC (a) was chosen for v5: same accuracy, simpler, and no feature the model never saw missing.
# MAGIC Run before the new-bank exclusion, so later re-runs give slightly different numbers.
# MAGIC
# MAGIC Experiment only: runs are logged to MLflow with tag `purpose=cblr_variant_comparison`
# MAGIC and **nothing is registered**. Also reports the foreign-office exclusion.

# COMMAND ----------

# MAGIC %pip install --quiet "mlflow==2.22.5" "scikit-learn==1.5.2"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

# MAGIC %run ./_features

# COMMAND ----------

import math

import mlflow
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

NOTEBOOK = "04b_compare_cblr_variants"
SEED = 42
TRAIN, TEST = (2001, 2019), (2020, 2024)

frame = build_model_frame()

# COMMAND ----------

# MAGIC %md ## Foreign offices: who is caught, and what the exclusion removes from training

# COMMAND ----------

gold = spark.table(f"{GOLD}.bank_quarter")
backup_only = (
    gold.groupBy("cert", "bank_name", "state_code")
    .agg(F.max(F.col("iba_flag")).alias("ever_iba"),
         F.sum(F.when(F.col("foreign_office_rule") == "zero capital backup rule", 1).otherwise(0)).alias("backup_quarters"),
         F.min(F.when(F.col("is_foreign_office"), F.col("report_date"))).alias("first_q"),
         F.max(F.when(F.col("is_foreign_office"), F.col("report_date"))).alias("last_q"))
    .filter("backup_quarters > 0 and ever_iba = 0")
    .orderBy("cert")
)
backup_rows = [r.asDict() for r in backup_only.collect()]
display(backup_only)

raw_t1 = spark.table(f"{SILVER}.fdic_financials").select("cert", "report_date",
                                                          F.col("tier1_leverage_pct").alias("raw_tier1"))
would_be = (
    frame.filter(F.col("is_foreign_office") & F.col("outcome_observed") & F.col("label_complete")
                 & (F.col("total_assets_k") > 0) & ~F.col("is_distressed")
                 & F.year("report_date").between(*TRAIN))
    .join(raw_t1, ["cert", "report_date"], "left")
)
fo = would_be.agg(
    F.count("*").alias("rows"),
    F.countDistinct("cert").alias("banks"),
    F.sum("distress_next_4q").alias("labelled_distressed"),
    # v4 saw the FDIC 0% Tier 1 as "already in distress", so these rows were already outside v4 training
    F.sum(F.when(F.coalesce(F.col("raw_tier1"), F.lit(0.0)) < TIER1_THRESHOLD, 1).otherwise(0)).alias("already_out_in_v4"),
).first().asDict()
print("Foreign-office rows removed from 2001–2019 training:", fo)

# COMMAND ----------

# MAGIC %md ## Train both variants on the same split

# COMMAND ----------

pdf = frame.filter("is_model_row").select(
    "cert", "report_date", *FEATURES, *RISK_BASED_CAPITAL_FEATURES, "cblr_reporter_flag", "distress_next_4q").toPandas()
pdf["year"] = pd.to_datetime(pdf["report_date"]).dt.year
cols = FEATURES + RISK_BASED_CAPITAL_FEATURES + ["cblr_reporter_flag"]
pdf[cols] = pdf[cols].astype("float64")
y = pdf["distress_next_4q"].astype(int)
tr = pdf["year"].between(*TRAIN)
te = pdf["year"].between(*TEST)
cblr_te = (pdf.loc[te, "cblr_reporter_flag"] == 1).values

VARIANTS = {
    # FEATURES no longer contains the risk-based ratios (variant (a) was chosen for v5)
    "a_drop_risk_based": FEATURES,
    "b_nan_plus_flag": FEATURES + RISK_BASED_CAPITAL_FEATURES + ["cblr_reporter_flag"],
}


def make_gbt():  # identical to 04_train_model
    return HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=500, max_leaf_nodes=31, min_samples_leaf=200,
        l2_regularization=1.0, early_stopping=True, validation_fraction=0.1,
        n_iter_no_change=30, random_state=SEED,
    )


def evaluate(yv, p):
    yv = np.asarray(yv)
    k = math.ceil(0.10 * len(yv))
    capture = yv[np.argsort(-p)[:k]].sum() / max(yv.sum(), 1)
    return {"roc_auc": roc_auc_score(yv, p), "pr_auc": average_precision_score(yv, p),
            "brier": brier_score_loss(yv, p), "top_decile_capture": capture,
            "base_rate": yv.mean(), "mean_pred": float(np.mean(p)),
            "rows": float(len(yv)), "positives": float(yv.sum())}


def calibration(yv, p, bins=10):
    d = pd.DataFrame({"y": np.asarray(yv), "p": p})
    d["bin"] = pd.qcut(d["p"].rank(method="first"), bins, labels=False)
    return d.groupby("bin").agg(mean_pred=("p", "mean"), observed=("y", "mean"), rows=("y", "size")).round(4)


mlflow.set_registry_uri("databricks-uc")
user = spark.sql("select current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/bank_health_distress_model")

out = {}
with mlflow.start_run(run_name=f"cblr_variants_{RUN_TS:%Y%m%d_%H%M}", tags={"purpose": "cblr_variant_comparison"}):
    mlflow.log_params({"train": str(TRAIN), "test": str(TEST), "model_rows": len(pdf),
                       "foreign_rows_removed_2001_2019": fo["rows"]})
    for name, feats in VARIANTS.items():
        with mlflow.start_run(run_name=name, nested=True, tags={"purpose": "cblr_variant_comparison"}):
            m = make_gbt().fit(pdf.loc[tr, feats], y[tr])
            p = m.predict_proba(pdf.loc[te, feats])[:, 1]
            res = {
                "test": evaluate(y[te], p),
                "test_cblr_banks": evaluate(y[te][cblr_te], p[cblr_te]),
                "test_other_banks": evaluate(y[te][~cblr_te], p[~cblr_te]),
            }
            for split, mets in res.items():
                mlflow.log_metrics({f"{split}_{k}": v for k, v in mets.items()})
            mlflow.log_params({"variant": name, "n_features": len(feats), "gbt_iterations_used": int(m.n_iter_)})
            cal = calibration(y[te], p)
            mlflow.log_table(cal.reset_index(), "calibration_test.json")
            out[name] = {"metrics": {s: {k: round(v, 4) for k, v in r.items()} for s, r in res.items()},
                         "calibration": cal.reset_index().to_dict("records"),
                         "iterations": int(m.n_iter_)}
            print(name, json.dumps(out[name]["metrics"]["test"]))

# Training rows with a CBLR flag or a missing risk-based ratio: CBLR did not exist before 2020
train_cblr_rows = int((pdf.loc[tr, "cblr_reporter_flag"] == 1).sum())
train_rbc_missing = int(pdf.loc[tr, "total_risk_based_capital_pct"].isna().sum())

# v4 reference: its logged test metrics (older frame: CBLR ratios were 0, foreign offices included)
v4 = (spark.table(f"{ML}.model_metrics")
      .filter("model = 'gradient_boosting' and split = 'test'")
      .withColumn("rk", F.dense_rank().over(Window.orderBy(F.col("run_ts").desc())))
      .filter("rk = 1").select("metric", "value").toPandas().set_index("metric")["value"].round(4).to_dict())

# COMMAND ----------

summary = {
    "foreign_offices": {"removed_2001_2019": fo, "backup_rule_only": backup_rows},
    "variants": out,
    "v4_logged_test": v4,
    "train_rows_with_cblr_flag": train_cblr_rows,
    "train_rows_missing_total_rbc": train_rbc_missing,
    "test_rows_cblr": int(cblr_te.sum()),
    "data_quality": dq_finish(),
}
print(json.dumps(summary, indent=2, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
