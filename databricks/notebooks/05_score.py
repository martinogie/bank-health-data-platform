# Databricks notebook source
# MAGIC %md
# MAGIC # 05_score — score every bank for the latest quarter
# MAGIC
# MAGIC Loads `bank_health.ml.distress_model@champion` and scores each bank's latest call report:
# MAGIC - `distress_probability`: chance of distress or failure within the next 4 quarters.
# MAGIC - `risk_band` by percentile within the quarter: **High** (top 5%), **Elevated** (next 10%),
# MAGIC   **Watch** (next 25%), **Low** (rest). Banks already in distress get
# MAGIC   **Already in distress** (the model predicts the onset of distress, so they aren't ranked).
# MAGIC - `reason_1..3`: the metrics that raise the bank's risk most — measured as how much the
# MAGIC   probability falls if that one metric is set to the median of all banks this quarter.
# MAGIC
# MAGIC Output: `ml.risk_scores` and Parquet export `gold/exports/ml_risk_scores` for Snowflake.
# MAGIC *Portfolio model on public data — not a supervisory rating.*

# COMMAND ----------

# MAGIC %pip install --quiet "mlflow==2.22.5"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

# MAGIC %run ./_features

# COMMAND ----------

import mlflow
import numpy as np
import pandas as pd
from mlflow.tracking import MlflowClient

NOTEBOOK = "05_score"
MODEL_NAME = f"{CATALOG}.ml.distress_model"
mlflow.set_registry_uri("databricks-uc")

version = MlflowClient().get_model_version_by_alias(MODEL_NAME, "champion").version
model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@champion")
print(f"Scoring with {MODEL_NAME} v{version}")

# COMMAND ----------

frame = build_model_frame()
latest = frame.agg(F.max("report_date")).first()[0]
cur = frame.filter(F.col("report_date") == latest).toPandas()
cur[FEATURES] = cur[FEATURES].astype("float64")
X = cur[FEATURES]
cur["distress_probability"] = model.predict_proba(X)[:, 1]
print(f"Latest quarter {latest}: {len(cur):,} banks")

# COMMAND ----------

# MAGIC %md ## Risk bands and reasons

# COMMAND ----------

ranked = ~cur["is_distressed"]
cur["risk_percentile"] = np.nan
cur.loc[ranked, "risk_percentile"] = cur.loc[ranked, "distress_probability"].rank(pct=True)
cur["risk_rank"] = cur.loc[ranked, "distress_probability"].rank(ascending=False, method="min")


def band(row):
    if row["is_distressed"]:
        return "Already in distress"
    p = row["risk_percentile"]
    return "High" if p > 0.95 else "Elevated" if p > 0.85 else "Watch" if p > 0.60 else "Low"


cur["risk_band"] = cur.apply(band, axis=1)

# Reasons: for each feature, set it to this quarter's median and see how much the probability falls.
medians = X.median()
drops = pd.DataFrame(index=cur.index)
for f in FEATURES:
    Xf = X.copy()
    Xf[f] = medians[f]
    drops[f] = cur["distress_probability"] - model.predict_proba(Xf)[:, 1]


def fmt(v):
    return "n/a" if pd.isna(v) else f"{v:,.2f}"


reasons = []
for i in cur.index:
    top = drops.loc[i][drops.loc[i] > 0].sort_values(ascending=False).head(3)
    texts = [f"{f} = {fmt(X.at[i, f])} (median {fmt(medians[f])})" for f in top.index]
    reasons.append(texts + [None] * (3 - len(texts)))
cur[["reason_1", "reason_2", "reason_3"]] = pd.DataFrame(reasons, index=cur.index)

# COMMAND ----------

out_cols = ["cert", "report_date", "bank_name", "state_code", "asset_size_tier", "total_assets_k",
            "distress_probability", "risk_percentile", "risk_rank", "risk_band", "is_distressed",
            "reason_1", "reason_2", "reason_3",
            "tier1_leverage_pct", "noncurrent_loans_pct", "roa_pct", "cre_to_equity_pct"]
scores = cur[out_cols].copy()
scores["model_name"] = MODEL_NAME
scores["model_version"] = int(version)
scores["scored_at"] = pd.Timestamp(RUN_TS).tz_localize(None)

sdf = spark.createDataFrame(scores)
sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{ML}.risk_scores")
sdf = spark.table(f"{ML}.risk_scores")
sdf.write.mode("overwrite").parquet(f"{EXPORTS}/ml_risk_scores")

T = "ml.risk_scores"
dq_unique(sdf, T, ["cert"])
dq_not_null(sdf, T, ["cert", "distress_probability", "risk_band"])
dq_range(sdf, T, "distress_probability", low=0, high=1, level="error")
dq_check(T, "export row count matches table",
         abs(spark.read.parquet(f"{EXPORTS}/ml_risk_scores").count() - sdf.count()))

display(sdf.filter("risk_band = 'High'").orderBy(F.col("distress_probability").desc())
        .select("bank_name", "state_code", "asset_size_tier", "distress_probability", "reason_1", "reason_2")
        .limit(20))

# COMMAND ----------

dq_summary = dq_finish()
band_counts = scores["risk_band"].value_counts().to_dict()
summary = {
    "report_date": str(latest),
    "banks_scored": len(scores),
    "model": f"{MODEL_NAME} v{version}",
    "risk_bands": band_counts,
    "probability_quantiles": {q: round(float(scores.loc[~scores.is_distressed, "distress_probability"].quantile(q)), 4)
                              for q in (0.5, 0.85, 0.95, 0.99)},
    "data_quality": dq_summary,
}
print(json.dumps(summary, indent=2))
dbutils.notebook.exit(json.dumps(summary))
