# Databricks notebook source
# MAGIC %md
# MAGIC # 06_model_report — how much v5 beats a simple rule, and how it does by segment
# MAGIC
# MAGIC **Analysis only.** Not part of the weekly job, trains nothing, changes no model. It reads the
# MAGIC v5 `@champion` and prod's `ml.model_frame` **read-only** (the frame the v5 training run wrote,
# MAGIC so the test rows are exactly v5's), and writes results to *this* environment's catalog and
# MAGIC MLflow experiment only.
# MAGIC
# MAGIC 1. **Check:** the frame reproduces the model card (86,296 test rows, 4,761 distressed, v5
# MAGIC    PR-AUC 0.333). If not, the notebook stops: the frame is not v5's.
# MAGIC 2. **M-03 rule baseline:** rank banks by how close they are to the nearest distress threshold
# MAGIC    (non-current loans 3%, Tier 1 leverage 5%). Two ways to measure "close" are tried; the one
# MAGIC    with the better PR-AUC on **2001–2019** is used, then judged once on 2020–2024.
# MAGIC 3. **Intervals:** 95% bootstrap intervals that resample **banks**, not bank-quarters (one bank's
# MAGIC    quarters are not independent). The headline is the *paired* difference v5 minus rule.
# MAGIC 4. **M-07 segments:** ROC-AUC, PR-AUC and top-10% capture by asset-size tier, Census region and
# MAGIC    CBLR reporting, with rows, positives and distinct distressed banks. Fewer than 30
# MAGIC    positives = too small to judge; cases from fewer than 10 distinct banks = too thin to judge.
# MAGIC    Capture in a segment = share of that segment's distress cases in its own riskiest 10%,
# MAGIC    also shown as a share of the most it could be (below 100% when the base rate is over 10%).
# MAGIC    Lift = PR-AUC / base rate, so segments with different base rates can be compared.
# MAGIC    For each tier, which distress rule made the cases positive.
# MAGIC
# MAGIC *Portfolio model on public data, not a supervisory rating.*

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

import math

import mlflow
import numpy as np
import pandas as pd
from mlflow.tracking import MlflowClient
from sklearn.metrics import average_precision_score, roc_auc_score

NOTEBOOK = "06_model_report"
require_pinned_environment()
mlflow.set_registry_uri("databricks-uc")

SEED = 42
N_BOOT = 1000
MIN_POSITIVES = 30  # a segment with fewer distress cases is too small to judge
MIN_DISTRESSED_BANKS = 10  # cases from fewer distinct banks are too thin to judge
TRAIN_YEARS = (2001, 2019)
TEST_YEARS = (2020, 2024)
PROD_FRAME = f"{ENV_CATALOGS['prod']}.ml.model_frame"  # read-only
# What the model card reports for v5 on the test rows; the frame must reproduce it
EXPECTED = {"rows": 86_296, "positives": 4_761, "pr_auc": 0.333}
# Strict-label challenger, from the v5 training run (model card). Not refitted: no new training.
STRICT_CHALLENGER = {
    "label": "failure within 12 months, Tier 1 < 5% or non-current loans > 3% (no two-losses rule)",
    "judged_on_main_label": {"roc_auc": 0.764, "pr_auc": 0.233, "top_decile_capture": 0.401},
    "judged_on_own_label": {
        "roc_auc": 0.858,
        "pr_auc": 0.243,
        "top_decile_capture": 0.562,
        "base_rate": 0.031,
    },
}

# Census regions by state (DC counts as South; territories kept apart)
CENSUS_REGION = {
    **dict.fromkeys(["CT", "ME", "MA", "NH", "RI", "VT", "NJ", "NY", "PA"], "Northeast"),
    **dict.fromkeys(["IL", "IN", "MI", "OH", "WI", "IA", "KS", "MN", "MO", "NE", "ND", "SD"], "Midwest"),
    **dict.fromkeys(
        [
            "DE",
            "DC",
            "FL",
            "GA",
            "MD",
            "NC",
            "SC",
            "VA",
            "WV",
            "AL",
            "KY",
            "MS",
            "TN",
            "AR",
            "LA",
            "OK",
            "TX",
        ],
        "South",
    ),
    **dict.fromkeys(["AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY", "AK", "CA", "HI", "OR", "WA"], "West"),
}

# COMMAND ----------

# MAGIC %md ## 1. The v5 test rows, scored by the champion

# COMMAND ----------

client = MlflowClient()
mv = client.get_model_version_by_alias(CHAMPION_MODEL, "champion")
model = mlflow.sklearn.load_model(f"models:/{CHAMPION_MODEL}@champion")
model_features = list(getattr(model, "feature_names_in_", FEATURES))

cols = ["cert", "report_date", "state_code", "asset_size_tier", "cblr_reporter", "distress_next_4q"]
pdf = (
    spark.table(PROD_FRAME)
    .filter("is_model_row")
    .select(*cols, *[c for c in model_features if c not in cols])
    .toPandas()
)
pdf["year"] = pd.to_datetime(pdf["report_date"]).dt.year
pdf["y"] = pdf["distress_next_4q"].astype(int)
train = pdf[pdf["year"].between(*TRAIN_YEARS)].reset_index(drop=True)
test = pdf[pdf["year"].between(*TEST_YEARS)].reset_index(drop=True)
test["p_v5"] = model.predict_proba(prepare_features(test, model_features))[:, 1]


def capture(y, p, share=0.10):
    """Same definition as training: distress cases among the riskiest ceil(10%) rows."""
    y = np.asarray(y)
    k = math.ceil(share * len(y))
    top = np.argsort(-np.asarray(p), kind="stable")[:k]
    return y[top].sum() / max(y.sum(), 1)


v5_point = {
    "rows": len(test),
    "positives": int(test["y"].sum()),
    "roc_auc": roc_auc_score(test["y"], test["p_v5"]),
    "pr_auc": average_precision_score(test["y"], test["p_v5"]),
    "top_decile_capture": capture(test["y"], test["p_v5"]),
}
print(f"{CHAMPION_MODEL} v{mv.version} on {PROD_FRAME}: {v5_point}")
mismatch = [k for k in ("rows", "positives") if v5_point[k] != EXPECTED[k]] + (
    ["pr_auc"] if abs(v5_point["pr_auc"] - EXPECTED["pr_auc"]) > 0.0015 else []
)
if mismatch:
    raise ValueError(
        f"{PROD_FRAME} does not reproduce the model card's v5 test set ({mismatch}): {v5_point}. "
        "The frame was probably rebuilt after v5 was trained; stop and check before reporting."
    )

# COMMAND ----------

# MAGIC %md ## 2. Rule baseline: distance to the nearest threshold
# MAGIC
# MAGIC Model rows are banks not yet in distress, so non-current loans are at most 3% and Tier 1 at
# MAGIC least 5%. The rule's risk score is minus the smaller of the two distances, so the bank
# MAGIC closest to either line ranks first. A missing ratio counts as far from its line; a bank with
# MAGIC both missing ranks last.
# MAGIC
# MAGIC - **relative:** distance as a share of the threshold, (3 − NCL) / 3 and (Tier 1 − 5) / 5
# MAGIC - **spread:** distance in units of the ratio's typical spread on 2001–2019 (IQR / 1.349)

# COMMAND ----------


def iqr_spread(s):
    q1, q3 = np.nanpercentile(s, [25, 75])
    return (q3 - q1) / 1.349


SPREAD = {
    "ncl": iqr_spread(train["noncurrent_loans_pct"]),
    "t1": iqr_spread(train["tier1_leverage_pct"]),
}


def rule_score(df, scaling):
    ncl_gap = NCL_THRESHOLD - df["noncurrent_loans_pct"]
    t1_gap = df["tier1_leverage_pct"] - TIER1_THRESHOLD
    if scaling == "relative":
        d_ncl, d_t1 = ncl_gap / NCL_THRESHOLD, t1_gap / TIER1_THRESHOLD
    else:
        d_ncl, d_t1 = ncl_gap / SPREAD["ncl"], t1_gap / SPREAD["t1"]
    nearest = np.fmin(d_ncl.to_numpy(dtype=float), d_t1.to_numpy(dtype=float))  # ignores one NaN
    nearest = np.where(np.isnan(nearest), np.inf, nearest)
    return -nearest


choice = {
    s: average_precision_score(train["y"], np.nan_to_num(rule_score(train, s), neginf=-1e9))
    for s in ("relative", "spread")
}
RULE_SCALING = max(choice, key=choice.get)
print(f"Rule PR-AUC on 2001–2019: {choice}; using '{RULE_SCALING}' (spreads {SPREAD})")
test["p_rule"] = np.nan_to_num(rule_score(test, RULE_SCALING), neginf=-1e9)

# COMMAND ----------

# MAGIC %md ## 3. Bootstrap by bank

# COMMAND ----------

certs, cert_idx = np.unique(test["cert"].to_numpy(), return_inverse=True)
rng = np.random.default_rng(SEED)
# Each replicate: draw banks with replacement; a bank drawn k times counts k times (weight k)
BOOT_WEIGHTS = [
    np.bincount(rng.integers(0, len(certs), len(certs)), minlength=len(certs)) for _ in range(N_BOOT)
]


def weighted_capture(y, p, w, share=0.10):
    order = np.argsort(-p, kind="stable")
    y, w = y[order], w[order]
    cum = np.cumsum(w)
    top = cum <= math.ceil(share * cum[-1])
    return (w[top] * y[top]).sum() / max((w * y).sum(), 1)


def metrics(y, p, w=None):
    if w is None:
        w = np.ones(len(y))
    keep = w > 0
    y, p, w = y[keep], p[keep], w[keep]
    out = {"pr_auc": np.nan, "roc_auc": np.nan, "top_decile_capture": np.nan}
    if y.min() == y.max():
        return out
    out["pr_auc"] = average_precision_score(y, p, sample_weight=w)
    out["roc_auc"] = roc_auc_score(y, p, sample_weight=w)
    out["top_decile_capture"] = weighted_capture(y, p, w)
    return out


def boot(rows, scores):
    """Bootstrap metrics per score column for the given rows; returns {score: DataFrame}."""
    y = rows["y"].to_numpy()
    idx = cert_idx[rows.index.to_numpy()]
    res = {s: [] for s in scores}
    for bw in BOOT_WEIGHTS:
        w = bw[idx].astype(float)
        for s in scores:
            res[s].append(metrics(y, rows[s].to_numpy(), w))
    return {s: pd.DataFrame(v) for s, v in res.items()}


def ci(series):
    lo, hi = np.nanpercentile(series, [2.5, 97.5])
    return float(lo), float(hi)


b = boot(test, ["p_v5", "p_rule"])
rule_point = metrics(test["y"].to_numpy(), test["p_rule"].to_numpy())
rule_point["top_decile_capture"] = capture(test["y"], test["p_rule"])
headline = []
for metric in ("pr_auc", "top_decile_capture", "roc_auc"):
    diff = b["p_v5"][metric] - b["p_rule"][metric]
    headline.append(
        {
            "metric": metric,
            "v5": v5_point[metric],
            "v5_ci_low": ci(b["p_v5"][metric])[0],
            "v5_ci_high": ci(b["p_v5"][metric])[1],
            "rule": rule_point[metric],
            "rule_ci_low": ci(b["p_rule"][metric])[0],
            "rule_ci_high": ci(b["p_rule"][metric])[1],
            "v5_minus_rule": v5_point[metric] - rule_point[metric],
            "diff_ci_low": ci(diff)[0],
            "diff_ci_high": ci(diff)[1],
            "share_of_replicates_v5_better": float((diff > 0).mean()),
        }
    )
headline = pd.DataFrame(headline)
display(headline)

# COMMAND ----------

# MAGIC %md ## 4. Segments (v5 only)

# COMMAND ----------

test["region"] = test["state_code"].map(CENSUS_REGION).fillna("Territories / unknown")
test["cblr"] = test["cblr_reporter"].map({True: "CBLR", False: "Not CBLR"}).fillna("Not reported")
SEGMENTS = {"asset_size_tier": "asset_size_tier", "region": "region", "cblr": "cblr"}

seg_rows = []
for dim, col in SEGMENTS.items():
    for value, rows in test.groupby(col):
        positives = int(rows["y"].sum())
        point = metrics(rows["y"].to_numpy(), rows["p_v5"].to_numpy())
        point["top_decile_capture"] = capture(rows["y"], rows["p_v5"]) if positives else np.nan
        distressed_banks = int(rows.loc[rows["y"] == 1, "cert"].nunique())
        base_rate = positives / len(rows)
        # The riskiest 10% can hold at most ceil(10% of rows) cases: below 100% when base rate > 10%
        capture_max = min(1.0, math.ceil(0.10 * len(rows)) / positives) if positives else np.nan
        row = {
            "dimension": dim,
            "segment": str(value),
            "rows": len(rows),
            "banks": rows["cert"].nunique(),
            "positives": positives,
            "distressed_banks": distressed_banks,
            "base_rate": base_rate,
            "too_small_to_judge": positives < MIN_POSITIVES,
            "too_thin_to_judge": distressed_banks < MIN_DISTRESSED_BANKS,
            **point,
            "pr_auc_lift": point["pr_auc"] / base_rate if positives else np.nan,
            "capture_max_possible": capture_max,
            "capture_share_of_max": point["top_decile_capture"] / capture_max if positives else np.nan,
        }
        if positives >= MIN_POSITIVES:
            bs = boot(rows, ["p_v5"])["p_v5"]
            for m in ("roc_auc", "pr_auc", "top_decile_capture"):
                row[f"{m}_ci_low"], row[f"{m}_ci_high"] = ci(bs[m])
        seg_rows.append(row)
segments = pd.DataFrame(seg_rows)
display(segments)

# COMMAND ----------

# MAGIC %md ### Which distress rule made each test case positive, by asset-size tier
# MAGIC
# MAGIC The label is 1 if, in any of the next 4 quarters, non-current loans > 3%, Tier 1 < 5%, or a
# MAGIC quarterly loss followed a quarterly loss (two losses in a row), or if the bank fails within 12
# MAGIC months. The triggers are rebuilt from the same frame; a case can have several.

# COMMAND ----------

all_q = (
    spark.table(PROD_FRAME)
    .select(
        "cert", "report_date", "noncurrent_loans_pct", "tier1_leverage_pct", "net_income_qtr_to_assets_pct"
    )
    .toPandas()
)
d = pd.to_datetime(all_q["report_date"])
all_q["q_idx"] = d.dt.year * 4 + d.dt.quarter
all_q["loss"] = all_q["net_income_qtr_to_assets_pct"] < 0
prev = all_q[["cert", "q_idx", "loss"]].assign(q_idx=all_q["q_idx"] + 1).rename(columns={"loss": "loss_prev"})
all_q = all_q.merge(prev, on=["cert", "q_idx"], how="left")
all_q["t_ncl"] = all_q["noncurrent_loans_pct"] > NCL_THRESHOLD
all_q["t_tier1"] = all_q["tier1_leverage_pct"] < TIER1_THRESHOLD
all_q["t_two_losses"] = all_q["loss"] & all_q["loss_prev"].fillna(False).astype(bool)
flags = all_q.set_index(["cert", "q_idx"])[["t_ncl", "t_tier1", "t_two_losses"]]

fails = spark.table(PROD_FRAME).select("cert", "report_date", "fails_within_12m").toPandas()
pos = test.loc[test["y"] == 1, ["cert", "report_date", "asset_size_tier"]].merge(
    fails, on=["cert", "report_date"], how="left"
)
pd_pos = pd.to_datetime(pos["report_date"])
pos["q_idx"] = pd_pos.dt.year * 4 + pd_pos.dt.quarter
ahead = pd.concat(
    [pos.assign(q_idx=pos["q_idx"] + k)[["cert", "q_idx"]].assign(case=pos.index) for k in (1, 2, 3, 4)]
)
ahead = (
    ahead.join(flags, on=["cert", "q_idx"])
    .fillna(False)
    .groupby("case")[["t_ncl", "t_tier1", "t_two_losses"]]
    .any()
)
pos = pos.join(ahead)
pos["t_failure"] = pos["fails_within_12m"].fillna(0).astype(int) == 1
TRIGGERS = ["t_two_losses", "t_ncl", "t_tier1", "t_failure"]
pos["n_triggers"] = pos[TRIGGERS].sum(axis=1)
untriggered = int((pos["n_triggers"] == 0).sum())
if untriggered:
    raise ValueError(
        f"{untriggered} positive test cases have no trigger: the rebuild does not match the label"
    )
pos["two_losses_only"] = pos["t_two_losses"] & (pos["n_triggers"] == 1)
triggers = (
    pos.groupby("asset_size_tier")
    .agg(
        positives=("cert", "size"),
        distressed_banks=("cert", "nunique"),
        two_losses=("t_two_losses", "sum"),
        two_losses_only=("two_losses_only", "sum"),
        ncl_over_3=("t_ncl", "sum"),
        tier1_under_5=("t_tier1", "sum"),
        failure=("t_failure", "sum"),
    )
    .reset_index()
)
display(triggers)
LARGEST_TIER = sorted(test["asset_size_tier"].dropna().unique())[-1]
largest = pos[pos["asset_size_tier"] == LARGEST_TIER]
names = (
    spark.table(PROD_FRAME)
    .filter(F.col("cert").isin([int(c) for c in largest["cert"].unique()]))
    .groupBy("cert")
    .agg(F.max_by("bank_name", "report_date").alias("bank_name"))
    .toPandas()
)
largest_banks = (
    largest.groupby("cert")
    .agg(
        cases=("cert", "size"),
        two_losses=("t_two_losses", "sum"),
        ncl_over_3=("t_ncl", "sum"),
        tier1_under_5=("t_tier1", "sum"),
        failure=("t_failure", "sum"),
    )
    .reset_index()
    .merge(names, on="cert", how="left")
    .sort_values("cases", ascending=False)
)
display(largest_banks)
largest_banks = largest_banks.to_dict("records")

# COMMAND ----------

# MAGIC %md ## 5. Save (this environment only) and log to MLflow

# COMMAND ----------

summary = {
    "champion": f"{CHAMPION_MODEL} v{mv.version}",
    "frame": PROD_FRAME,
    "test_years": list(TEST_YEARS),
    "v5": v5_point,
    "rule": {"scaling": RULE_SCALING, "train_pr_auc_by_scaling": choice, "spreads": SPREAD, **rule_point},
    "strict_challenger": STRICT_CHALLENGER,
    "bootstrap": {"replicates": N_BOOT, "unit": "bank (cert)", "seed": SEED, "banks": len(certs)},
    "headline": headline.to_dict("records"),
    "segments": segments.to_dict("records"),
    "triggers_by_tier": triggers.to_dict("records"),
    "largest_tier_distressed_banks": largest_banks,
}

for name, df in (
    ("model_report_headline", headline),
    ("model_report_segments", segments),
    ("model_report_triggers", triggers),
):
    (
        spark.createDataFrame(df.assign(run_ts=RUN_TS, champion_version=int(mv.version)))
        .write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(f"{ML}.{name}")
    )

user = spark.sql("select current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/{EXPERIMENT_NAME}")
with mlflow.start_run(run_name="model_report_baselines_segments", tags={"role": "analysis", "phase": "3"}):
    mlflow.log_params({"champion_version": mv.version, "rule_scaling": RULE_SCALING, "bootstrap": N_BOOT})
    for r in headline.to_dict("records"):
        mlflow.log_metrics({f"{r['metric']}_v5": r["v5"], f"{r['metric']}_rule": r["rule"]})
    mlflow.log_dict(summary, "model_report.json")

dbutils.notebook.exit(json.dumps(summary, default=float))
