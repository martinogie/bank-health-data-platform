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
# MAGIC - `reason_1..3`: the reported metrics that raise the bank's risk most, written
# MAGIC   `feature = value (peer median m)`: ranked by their SHAP contribution to the bank's log-odds
# MAGIC   (shap `TreeExplainer`), compared with the median of banks in the same asset-size tier. A
# MAGIC   metric the bank doesn't report is never cited.
# MAGIC - `shap_waterfall` (JSON, scored banks): the 8 features that move the score most, either way:
# MAGIC   feature, bank value, peer median, push in percentage points of probability (they add up,
# MAGIC   with `other_pp`, from `base_pct` to the bank's probability), for the website's waterfall.
# MAGIC - `distress_lines` (JSON, banks already in distress): the lines crossed this quarter
# MAGIC   (non-current loans above 3%, Tier 1 below 5%, two quarterly losses), with value and threshold.
# MAGIC - `reason_method`: `shap`, or `median_fallback` when SHAP gives no usable reason (or fails):
# MAGIC   then the earlier method, `feature = value (median m)`, how much the probability falls if
# MAGIC   that one metric is set to the median of all scored banks this quarter.
# MAGIC
# MAGIC The model uses FDIC call-report features only; CFPB complaints are context, not inputs.
# MAGIC
# MAGIC Output (`model_alias = champion`): `ml.risk_scores` and Parquet export
# MAGIC `gold/exports/ml_risk_scores` for Snowflake. Any other alias (e.g. `candidate`, for review
# MAGIC before promotion) writes `ml.risk_scores_<alias>` only and exports nothing.
# MAGIC *Portfolio model on public data — not a supervisory rating.*

# COMMAND ----------

# MAGIC %pip install --quiet "mlflow==2.22.5" "scikit-learn==1.5.2" "shap==0.46.0" "numba==0.60.0" "llvmlite==0.43.0"

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
require_pinned_environment()
mlflow.set_registry_uri("databricks-uc")

dbutils.widgets.text("model_alias", "champion", "Model alias to score with")
ALIAS = dbutils.widgets.get("model_alias").strip() or "champion"
PRODUCTION = ALIAS == "champion"
# @champion is always prod's model (dev reads it, read-only); other aliases are this environment's.
SCORING_MODEL = CHAMPION_MODEL if PRODUCTION else MODEL_NAME
SCORES_TABLE = f"{ML}.risk_scores" if PRODUCTION else f"{ML}.risk_scores_{ALIAS}"

client = MlflowClient()
mv = client.get_model_version_by_alias(SCORING_MODEL, ALIAS)
version = mv.version
model = mlflow.sklearn.load_model(f"models:/{SCORING_MODEL}@{ALIAS}")
cutoffs = cutoffs_from_tags(mv.tags)
model_features = list(getattr(model, "feature_names_in_", FEATURES))
unknown = sorted(set(model_features) - set(FEATURES))
if unknown:
    raise ValueError(f"v{version} expects features this code no longer builds: {unknown}")
print(f"Scoring with {SCORING_MODEL} v{version} (@{ALIAS}); band cut-offs {cutoffs}; writing {SCORES_TABLE}")

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
    for p, fo, nb, d in zip(
        cur["distress_probability"], cur["is_foreign_office"], cur["is_new_bank"], cur["is_distressed"]
    )
]
ranked = cur["risk_band"].isin(SCORED_BANDS)
cur["risk_percentile"] = np.nan
cur.loc[ranked, "risk_percentile"] = cur.loc[ranked, "distress_probability"].rank(pct=True)
cur["risk_rank"] = cur.loc[ranked, "distress_probability"].rank(ascending=False, method="min")

# Reasons, for scored banks only (M-05). First choice: SHAP, each feature's contribution to the
# bank's log-odds (shap TreeExplainer; pinned with the environment above), compared with the
# median of banks in the same asset-size tier. Fallback, per bank or for the whole run if SHAP
# fails: the earlier median method (set one feature to the median of scored banks, see how much
# the probability falls). Either way a feature the bank doesn't report is never cited.
# reason_method says which was used.
Xr = X[ranked]
medians = Xr.median()
drops = pd.DataFrame(index=Xr.index)
for f in model_features:
    Xf = Xr.copy()
    Xf[f] = medians[f]
    drops[f] = cur.loc[ranked, "distress_probability"] - model.predict_proba(Xf)[:, 1]

contributions, shap_error, base = None, None, None
try:
    import shap

    explainer = shap.TreeExplainer(model)
    values = explainer.shap_values(Xr)
    values = values[1] if isinstance(values, list) else values
    base = float(np.ravel(explainer.expected_value)[-1])
    gap = check_additivity(base, values, model.decision_function(Xr))
    contributions = pd.DataFrame(values, index=Xr.index, columns=model_features)
    print(f"SHAP {shap.__version__}: {len(Xr):,} banks explained, additivity gap {gap:.1e}")
except Exception as exc:  # the scores never depend on this; reasons fall back to the median method
    shap_error = f"{type(exc).__name__}: {exc}"
    print(f"SHAP reasons not available, using the median method for every bank: {shap_error}")
peers = tier_peer_medians(Xr, cur.loc[ranked, "asset_size_tier"])

reasons = pd.DataFrame([[None] * 3] * len(cur), index=cur.index, columns=["reason_1", "reason_2", "reason_3"])
cur["reason_method"] = None
for i in Xr.index:
    texts = (
        shap_reason_texts(Xr.loc[i], contributions.loc[i], peers.loc[i]) if contributions is not None else []
    )
    if any(texts):
        reasons.loc[i], cur.loc[i, "reason_method"] = texts, REASON_SHAP
    else:
        reasons.loc[i], cur.loc[i, "reason_method"] = (
            reason_texts(Xr.loc[i], drops.loc[i], medians),
            REASON_MEDIAN_FALLBACK,
        )
cur[["reason_1", "reason_2", "reason_3"]] = reasons
print("reason_method:", cur["reason_method"].value_counts(dropna=False).to_dict())

# For the website's Bank Detail page: the top 8 SHAP pushes (scored banks), as JSON text, and for
# banks already in distress, the distress lines they crossed (no reasons for them).
cur["shap_waterfall"] = None
if contributions is not None:
    for i in Xr.index:
        cur.loc[i, "shap_waterfall"] = json.dumps(
            shap_waterfall(Xr.loc[i], contributions.loc[i], peers.loc[i], base)
        )
cur["distress_lines"] = None
in_distress = cur["risk_band"] == ALREADY_IN_DISTRESS
for i in cur.index[in_distress]:
    r = cur.loc[i]
    lines = distress_lines(
        r["noncurrent_loans_pct"],
        r["tier1_leverage_pct"],
        r["is_loss_qtr"],
        r["is_loss_qtr_lag1"],
        NCL_THRESHOLD,
        TIER1_THRESHOLD,
    )
    cur.loc[i, "distress_lines"] = json.dumps(lines)
print(f"waterfalls: {cur['shap_waterfall'].notna().sum():,}; distress lines: {in_distress.sum():,} banks")

# COMMAND ----------

out_cols = [
    "cert",
    "report_date",
    "bank_name",
    "state_code",
    "asset_size_tier",
    "total_assets_k",
    "distress_probability",
    "risk_percentile",
    "risk_rank",
    "risk_band",
    "is_distressed",
    "is_new_bank",
    "is_foreign_office",
    "cblr_reporter",
    "bank_age_years",
    "reason_1",
    "reason_2",
    "reason_3",
    "reason_method",
    "shap_waterfall",
    "distress_lines",
    "tier1_leverage_pct",
    "noncurrent_loans_pct",
    "roa_pct",
    "cre_to_equity_pct",
]
scores = cur[out_cols].copy()
scores["model_name"] = SCORING_MODEL
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
dq_check(
    T,
    "every scored bank has a probability",
    sdf.filter(
        F.col("risk_band").isin(SCORED_BANDS + [ALREADY_IN_DISTRESS]) & F.col("distress_probability").isNull()
    ).count(),
)
dq_check(
    T,
    "not-scored banks have no probability",
    sdf.filter(
        F.col("risk_band").isin(NEW_BANK, FOREIGN_OFFICE) & F.col("distress_probability").isNotNull()
    ).count(),
)
dq_check(T, "risk_band is a known band", sdf.filter(~F.col("risk_band").isin(ALL_BANDS)).count())
dq_range(sdf, T, "distress_probability", low=0, high=1, level="error")
dq_check(
    T,
    "no reason mentions total risk-based capital",
    sdf.filter(F.concat_ws(" ", "reason_1", "reason_2", "reason_3").contains("total_risk_based")).count(),
)
dq_check(
    T,
    "reason_method is shap or median_fallback for every scored bank, empty otherwise",
    sdf.filter(
        (F.col("risk_band").isin(SCORED_BANDS) & ~F.col("reason_method").isin(list(REASON_METHODS)))
        | (~F.col("risk_band").isin(SCORED_BANDS) & F.col("reason_method").isNotNull())
    ).count(),
)
dq_check(
    T,
    "reasons come from SHAP (not the fallback) for at least 95% of scored banks",
    0 if shap_error is None and (cur.loc[ranked, "reason_method"] == REASON_SHAP).mean() >= 0.95 else 1,
    level="warn",
    detail=shap_error or "",
)
if PRODUCTION:
    dq_check(
        T,
        "export row count matches table",
        abs(spark.read.parquet(f"{EXPORTS}/ml_risk_scores").count() - sdf.count()),
    )

top20 = (
    scores[ranked]
    .sort_values("distress_probability", ascending=False)
    .head(20)[
        [
            "cert",
            "bank_name",
            "state_code",
            "asset_size_tier",
            "bank_age_years",
            "distress_probability",
            "risk_band",
            "tier1_leverage_pct",
            "reason_1",
            "reason_2",
            "reason_3",
            "reason_method",
        ]
    ]
)
display(top20)

# COMMAND ----------

dq_summary = dq_finish()
band_counts = scores["risk_band"].value_counts().reindex(ALL_BANDS, fill_value=0).to_dict()
summary = {
    "report_date": str(latest),
    "banks": len(scores),
    "model": f"{SCORING_MODEL} v{version} (@{ALIAS})",
    "table": SCORES_TABLE,
    "band_cutoffs": cutoffs,
    "risk_bands": {k: int(v) for k, v in band_counts.items()},
    "probability_quantiles": {
        q: round(float(scores.loc[ranked, "distress_probability"].quantile(q)), 4)
        for q in (0.5, 0.85, 0.95, 0.99)
    },
    "top20": top20.round(4).to_dict("records"),
    "reason_methods": {str(k): int(v) for k, v in cur.loc[ranked, "reason_method"].value_counts().items()},
    "shap_error": shap_error,
    "data_quality": dq_summary,
}
print(json.dumps(summary, indent=2, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
