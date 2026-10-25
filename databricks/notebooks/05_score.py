# Databricks notebook source
# MAGIC %md
# MAGIC # 05_score — score every bank for the latest quarter
# MAGIC
# MAGIC Loads `bank_health.ml.distress_model@champion` (or another alias, see `model_alias`) and
# MAGIC scores each bank's latest call report:
# MAGIC - `distress_probability`: chance of distress or failure within the next 4 quarters.
# MAGIC - `risk_band` from fixed probability cut-offs stored on the model version (see
# MAGIC   `_scoring_rules`): **High** (≥ 50% of such banks became distressed, out of sample on
# MAGIC   2001–2019), **Elevated** (25–50%), **Watch** (10–25%), **Low** (< 10%).
# MAGIC   Not scored: **Office of a foreign bank – not scored** (no capital ratios),
# MAGIC   **New bank – not scored** (under 3 years old); and **Already in distress** (the model
# MAGIC   predicts the onset of distress).
# MAGIC - `reason_1..3`: the reported metrics that raise the bank's risk most — how much the
# MAGIC   probability falls if that one metric is set to the median of all scored banks this quarter.
# MAGIC
# MAGIC The model uses FDIC call-report features only; CFPB complaints are context, not inputs.
# MAGIC
# MAGIC Output (`model_alias = champion`): `ml.risk_scores` and Parquet export
# MAGIC `gold/exports/ml_risk_scores` for Snowflake. Any other alias (e.g. `candidate`, for review
# MAGIC before promotion) writes `ml.risk_scores_<alias>` only and exports nothing.
# MAGIC *Portfolio model on public data — not a supervisory rating.*

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

import mlflow
import numpy as np
import pandas as pd
from mlflow.tracking import MlflowClient

NOTEBOOK = "05_score"
MODEL_NAME = f"{CATALOG}.ml.distress_model"
require_pinned_environment()
mlflow.set_registry_uri("databricks-uc")

dbutils.widgets.text("model_alias", "champion", "Model alias to score with")
ALIAS = dbutils.widgets.get("model_alias").strip() or "champion"
PRODUCTION = ALIAS == "champion"
SCORES_TABLE = f"{ML}.risk_scores" if PRODUCTION else f"{ML}.risk_scores_{ALIAS}"

client = MlflowClient()
mv = client.get_model_version_by_alias(MODEL_NAME, ALIAS)
version = mv.version
model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{ALIAS}")
cutoffs = cutoffs_from_tags(mv.tags)
model_features = list(getattr(model, "feature_names_in_", FEATURES))
unknown = sorted(set(model_features) - set(FEATURES))
if unknown:
    raise ValueError(f"v{version} expects features this code no longer builds: {unknown}")
print(f"Scoring with {MODEL_NAME} v{version} (@{ALIAS}); band cut-offs {cutoffs}; writing {SCORES_TABLE}")

# COMMAND ----------

frame = build_model_frame()
latest = frame.agg(F.max("report_date")).first()[0]
cur = frame.filter(F.col("report_date") == latest).toPandas()
X = prepare_features(cur, model_features)

# Banks that are not scored get no probability at all
scored_or_distressed = ~cur["is_foreign_office"] & ~cur["is_new_bank"]
cur["distress_probability"] = np.nan
cur.loc[scored_or_distressed, "distress_probability"] = model.predict_proba(X[scored_or_distressed])[:, 1]
print(f"Latest quarter {latest}: {len(cur):,} banks")

# COMMAND ----------

# MAGIC %md ## Risk bands and reasons

# COMMAND ----------

cur["risk_band"] = [
    assign_band(p, fo, nb, d, cutoffs)
    for p, fo, nb, d in zip(cur["distress_probability"], cur["is_foreign_office"], cur["is_new_bank"], cur["is_distressed"])
]
ranked = cur["risk_band"].isin(SCORED_BANDS)
cur["risk_percentile"] = np.nan
cur.loc[ranked, "risk_percentile"] = cur.loc[ranked, "distress_probability"].rank(pct=True)
cur["risk_rank"] = cur.loc[ranked, "distress_probability"].rank(ascending=False, method="min")

# Reasons, for scored banks only: set one feature to the median of scored banks, see how much
# the probability falls. Features the bank doesn't report are never used as reasons.
Xr = X[ranked]
medians = Xr.median()
drops = pd.DataFrame(index=Xr.index)
for f in model_features:
    Xf = Xr.copy()
    Xf[f] = medians[f]
    drops[f] = cur.loc[ranked, "distress_probability"] - model.predict_proba(Xf)[:, 1]

reasons = pd.DataFrame([[None] * 3] * len(cur), index=cur.index, columns=["reason_1", "reason_2", "reason_3"])
for i in Xr.index:
    reasons.loc[i] = reason_texts(Xr.loc[i], drops.loc[i], medians)
cur[["reason_1", "reason_2", "reason_3"]] = reasons

# COMMAND ----------

out_cols = ["cert", "report_date", "bank_name", "state_code", "asset_size_tier", "total_assets_k",
            "distress_probability", "risk_percentile", "risk_rank", "risk_band", "is_distressed",
            "is_new_bank", "is_foreign_office", "cblr_reporter", "bank_age_years",
            "reason_1", "reason_2", "reason_3",
            "tier1_leverage_pct", "noncurrent_loans_pct", "roa_pct", "cre_to_equity_pct"]
scores = cur[out_cols].copy()
scores["model_name"] = MODEL_NAME
scores["model_version"] = int(version)
scores["scored_at"] = pd.Timestamp(RUN_TS).tz_localize(None)

sdf = spark.createDataFrame(scores)
sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(SCORES_TABLE)
sdf = spark.table(SCORES_TABLE)
if PRODUCTION:
    sdf.write.mode("overwrite").parquet(f"{EXPORTS}/ml_risk_scores")

T = SCORES_TABLE.split(".", 1)[1]
dq_unique(sdf, T, ["cert"])
dq_not_null(sdf, T, ["cert", "risk_band"])
dq_check(T, "every scored bank has a probability",
         sdf.filter(F.col("risk_band").isin(SCORED_BANDS + [ALREADY_IN_DISTRESS])
                    & F.col("distress_probability").isNull()).count())
dq_check(T, "not-scored banks have no probability",
         sdf.filter(F.col("risk_band").isin(NEW_BANK, FOREIGN_OFFICE) & F.col("distress_probability").isNotNull()).count())
dq_check(T, "risk_band is a known band", sdf.filter(~F.col("risk_band").isin(ALL_BANDS)).count())
dq_range(sdf, T, "distress_probability", low=0, high=1, level="error")
dq_check(T, "no reason mentions total risk-based capital",
         sdf.filter(F.concat_ws(" ", "reason_1", "reason_2", "reason_3").contains("total_risk_based")).count())
if PRODUCTION:
    dq_check(T, "export row count matches table",
             abs(spark.read.parquet(f"{EXPORTS}/ml_risk_scores").count() - sdf.count()))

top20 = (scores[ranked].sort_values("distress_probability", ascending=False).head(20)
         [["cert", "bank_name", "state_code", "asset_size_tier", "bank_age_years", "distress_probability",
           "risk_band", "tier1_leverage_pct", "reason_1", "reason_2", "reason_3"]])
display(top20)

# COMMAND ----------

dq_summary = dq_finish()
band_counts = scores["risk_band"].value_counts().reindex(ALL_BANDS, fill_value=0).to_dict()
summary = {
    "report_date": str(latest),
    "banks": len(scores),
    "model": f"{MODEL_NAME} v{version} (@{ALIAS})",
    "table": SCORES_TABLE,
    "band_cutoffs": cutoffs,
    "risk_bands": {k: int(v) for k, v in band_counts.items()},
    "probability_quantiles": {q: round(float(scores.loc[ranked, "distress_probability"].quantile(q)), 4)
                              for q in (0.5, 0.85, 0.95, 0.99)},
    "top20": top20.round(4).to_dict("records"),
    "data_quality": dq_summary,
}
print(json.dumps(summary, indent=2, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
