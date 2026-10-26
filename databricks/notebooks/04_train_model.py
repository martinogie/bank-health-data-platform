# Databricks notebook source
# MAGIC %md
# MAGIC # 04_train_model — bank distress early-warning model (MLflow)
# MAGIC
# MAGIC Predicts the probability that a bank **not currently in distress** enters distress (or fails)
# MAGIC within the next 4 quarters. Features and labels: see `_features`.
# MAGIC
# MAGIC **Out-of-time evaluation** (no random splits, so the future never leaks into training):
# MAGIC 1. *Selection*: fit on 2001–2016, compare on 2017–2019 (validation) → pick by PR-AUC.
# MAGIC 2. *Final*: refit both models on 2001–2019, report on 2020–2024 (test, used once).
# MAGIC 3. **Band cut-offs** from time-ordered out-of-sample predictions on 2001–2019 only (each year
# MAGIC    predicted by a model fitted on earlier years whose labels were complete), so the test
# MAGIC    period never influences them. Targets: High precision ≥ 50%, Elevated ≥ 25%,
# MAGIC    Watch ≥ 2× the base rate.
# MAGIC 4. **Challenger** with a stricter label (failure, Tier 1 < 5% or non-current loans > 3%; no
# MAGIC    "two net losses"), logged in MLflow, not registered.
# MAGIC 5. Register the selected model in Unity Catalog as `bank_health.ml.distress_model` with alias
# MAGIC    `candidate`. It becomes `champion` only with `promote=true` (after a person approves it).
# MAGIC
# MAGIC Version 5 differs from v4 only by data fixes (no risk-based capital feature, foreign offices
# MAGIC and banks under 3 years excluded, CRE missing stays missing) and probability-based bands;
# MAGIC it is compared with v4 on the same test period and the same rows.
# MAGIC
# MAGIC Models: logistic regression (baseline) and gradient-boosted trees (scikit-learn
# MAGIC `HistGradientBoostingClassifier`). Metrics: ROC-AUC, PR-AUC, top-decile capture, Brier
# MAGIC score, calibration. *Portfolio model on public data — not a supervisory rating.*

# COMMAND ----------

# MAGIC %md
# MAGIC Pinned versions, identical in 05_score:
# MAGIC - MLflow 2.22.5: serverless ships 2.11, which can't upload models to Unity Catalog storage
# MAGIC   on Free Edition (access denied).
# MAGIC - scikit-learn 1.5.2: a pickled model only loads with the scikit-learn it was saved with,
# MAGIC   and one-off runs and scheduled jobs can get different serverless environments
# MAGIC   (Python 3.11 vs 3.12). Pinning keeps training and scoring in step.

# COMMAND ----------

# MAGIC %pip install --quiet "mlflow==2.22.5" "scikit-learn==1.5.2"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

# MAGIC %run ./_features

# COMMAND ----------

import inspect
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import numpy as np
import pandas as pd
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient
from sklearn.calibration import calibration_curve
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import QuantileTransformer

NOTEBOOK = "04_train_model"
require_pinned_environment()
MODEL_NAME = f"{CATALOG}.ml.distress_model"
SEED = 42
PERIODS = {
    "selection_train": (2001, 2016),
    "validation": (2017, 2019),
    "train": (2001, 2019),
    "test": (2020, 2024),
}

# COMMAND ----------

# The weekly job passes retrain=false: keep scoring with the current @champion and skip
# training (saves compute; the model only changes when retrained on purpose).
dbutils.widgets.text("retrain", "true", "Retrain model (true/false)")
dbutils.widgets.text("promote", "false", "Make the new version @champion (true/false)")
RETRAIN = dbutils.widgets.get("retrain").strip().lower() == "true"
PROMOTE = dbutils.widgets.get("promote").strip().lower() == "true"
# Band meanings = distress rate within the band, out of sample on the training years:
# High >= 50%, Elevated 25-50%, Watch 10-25%, Low < 10%
BAND_TARGETS = {"High": 0.50, "Elevated": 0.25, "Watch": 0.10}
WALK_FORWARD_FIRST_YEAR = 2003  # first year with 2 earlier years to train on
mlflow.set_registry_uri("databricks-uc")
if not RETRAIN:
    try:
        current = MlflowClient().get_model_version_by_alias(MODEL_NAME, "champion").version
    except Exception:
        current = None
    if current is not None:
        print(f"retrain=false: keeping {MODEL_NAME} v{current} (@champion); training skipped")
        dbutils.notebook.exit(json.dumps({"skipped": True, "champion_version": int(current)}))
    print("retrain=false but no @champion exists yet, so training anyway")

# COMMAND ----------

# MAGIC %md ## Build features and labels

# COMMAND ----------

frame = build_model_frame()
frame.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{ML}.model_frame")
frame = spark.table(f"{ML}.model_frame")

# FDIC's reported total risk-based capital (0 for CBLR banks), only to score v4 for comparison
raw_rbc = spark.table(f"{SILVER}.fdic_financials").select(
    "cert", "report_date", F.col("total_risk_based_capital_pct").alias("raw_total_rbc_pct")
)
pdf = (
    frame.filter("is_model_row")
    .join(raw_rbc, ["cert", "report_date"], "left")
    .select(
        "cert", "report_date", *FEATURES, "raw_total_rbc_pct", "distress_next_4q", "distress_strict_next_4q"
    )
    .toPandas()
)
y_strict = pdf["distress_strict_next_4q"].astype(int)
pdf["year"] = pd.to_datetime(pdf["report_date"]).dt.year
pdf[FEATURES] = pdf[FEATURES].astype("float64")
y_all = pdf["distress_next_4q"].astype(int)


def period(name):
    lo, hi = PERIODS[name]
    mask = pdf["year"].between(lo, hi)
    return pdf.loc[mask, FEATURES], y_all[mask], pdf.loc[mask, "year"]


summary_rows = (
    pdf.groupby("year")["distress_next_4q"]
    .agg(["count", "mean"])
    .rename(columns={"count": "bank_quarters", "mean": "distress_rate"})
)
display(summary_rows.reset_index())
print(f"Model rows: {len(pdf):,}  overall distress rate: {y_all.mean():.3%}")

T = "ml.model_frame"
dq_check(T, "model rows > 100k", int(len(pdf) < 100_000))
dq_check(
    T,
    "both classes present in train and test",
    int(any(period(p)[1].nunique() < 2 for p in ("train", "test"))),
)
dq_unique(frame, T, ["cert", "report_date"])
dq_check(
    T, "no risk-based capital ratio among features", len(set(FEATURES) & set(RISK_BASED_CAPITAL_FEATURES))
)
dq_check(
    T,
    "zero or negative Tier 1 (not a foreign office) always counts as distress",
    frame.filter("tier1_leverage_pct <= 0 and not is_foreign_office and not is_distressed").count(),
)

# What each exclusion removes from the 2001–2019 training years (rows that pass every other rule)
eligible = frame.filter(
    F.col("outcome_observed")
    & F.col("label_complete")
    & (F.col("total_assets_k") > 0)
    & ~F.col("is_distressed")
    & F.year("report_date").between(*PERIODS["train"])
)


def removed(cond):
    r = (
        eligible.filter(cond)
        .agg(
            F.count("*").alias("rows"),
            F.countDistinct("cert").alias("banks"),
            F.coalesce(F.sum("distress_next_4q"), F.lit(0)).alias("labelled_distressed"),
        )
        .first()
    )
    return r.asDict()


exclusions_2001_2019 = {
    "foreign_offices": removed("is_foreign_office"),
    "new_banks_under_3y": removed("is_new_bank and not is_foreign_office"),
    "capital_zero_review_kept": removed("capital_zero_review"),  # flagged for review, not excluded
}
print("Rows removed from 2001–2019 training:", json.dumps(exclusions_2001_2019))

# COMMAND ----------

# MAGIC %md ## Models and metrics

# COMMAND ----------


def make_logistic():
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            (
                "scale",
                QuantileTransformer(
                    output_distribution="normal", n_quantiles=1000, subsample=200_000, random_state=SEED
                ),
            ),
            ("model", LogisticRegression(C=1.0, max_iter=2000)),
        ]
    )


def make_gbt():
    return HistGradientBoostingClassifier(
        learning_rate=0.05,
        max_iter=500,
        max_leaf_nodes=31,
        min_samples_leaf=200,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.1,
        n_iter_no_change=30,
        random_state=SEED,
    )


MODELS = {"logistic_regression": make_logistic, "gradient_boosting": make_gbt}


def evaluate(y, p):
    y = np.asarray(y)
    k = math.ceil(0.10 * len(y))
    top = np.argsort(-p)[:k]
    capture = y[top].sum() / max(y.sum(), 1)
    return {
        "roc_auc": roc_auc_score(y, p),
        "pr_auc": average_precision_score(y, p),
        "brier": brier_score_loss(y, p),
        "top_decile_capture": capture,  # share of all distress cases in the riskiest 10%
        "top_decile_lift": capture / 0.10,
        "base_rate": y.mean(),
        "rows": float(len(y)),
        "positives": float(y.sum()),
    }


def log_model_kwargs():
    # MLflow 3 renamed artifact_path -> name
    return "name" if "name" in inspect.signature(mlflow.sklearn.log_model).parameters else "artifact_path"


# COMMAND ----------

# MAGIC %md ## 1. Selection on the 2017–2019 validation period

# COMMAND ----------

# Set the registry first: otherwise MLflow looks it up in a Spark config that serverless blocks.
mlflow.set_registry_uri("databricks-uc")
user = spark.sql("select current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/bank_health_distress_model")

X_sel, y_sel, _ = period("selection_train")
X_val, y_val, _ = period("validation")
X_tr, y_tr, _ = period("train")
X_te, y_te, year_te = period("test")

results = {}
parent = mlflow.start_run(run_name=f"distress_model_{RUN_TS:%Y%m%d_%H%M}")
mlflow.log_params(
    {
        "label": "distress (NCL>3% | Tier1<5% | 2 consecutive net losses) or failure within 4 quarters",
        "model_version_line": "v5: no risk-based capital feature; foreign offices and banks <3y excluded; "
        "CRE missing stays missing; probability bands from 2001-2019 out-of-sample",
        "excluded_foreign_rows_2001_2019": exclusions_2001_2019["foreign_offices"]["rows"],
        "excluded_new_bank_rows_2001_2019": exclusions_2001_2019["new_banks_under_3y"]["rows"],
        "ncl_threshold_pct": NCL_THRESHOLD,
        "tier1_threshold_pct": TIER1_THRESHOLD,
        "periods": str(PERIODS),
        "n_features": len(FEATURES),
        "model_rows": len(pdf),
    }
)
for name, make in MODELS.items():
    with mlflow.start_run(run_name=name, nested=True) as child:
        m = make().fit(X_sel, y_sel)
        val = evaluate(y_val, m.predict_proba(X_val)[:, 1])
        mlflow.log_metrics({f"validation_{k}": v for k, v in val.items()})
        results[name] = {"run_id": child.info.run_id, "validation": val}
        print(f"{name:20s} validation PR-AUC {val['pr_auc']:.3f}  ROC-AUC {val['roc_auc']:.3f}")

champion = max(results, key=lambda n: results[n]["validation"]["pr_auc"])
print(f"\nSelected on validation PR-AUC: {champion}")
mlflow.log_param("champion", champion)

# COMMAND ----------

# MAGIC %md ## 2. Refit on 2001–2019, test once on 2020–2024

# COMMAND ----------

final_models = {}
for name, make in MODELS.items():
    with mlflow.start_run(run_id=results[name]["run_id"], nested=True):
        m = make().fit(X_tr, y_tr)
        p_te = m.predict_proba(X_te)[:, 1]
        test = evaluate(y_te, p_te)
        train = evaluate(y_tr, m.predict_proba(X_tr)[:, 1])
        mlflow.log_metrics({f"test_{k}": v for k, v in test.items()})
        mlflow.log_metrics({f"train_{k}": v for k, v in train.items()})
        if name == "gradient_boosting":
            mlflow.log_param("gbt_iterations_used", int(m.n_iter_))
        results[name].update(test=test, train=train)
        final_models[name] = (m, p_te)
        print(
            f"{name:20s} test PR-AUC {test['pr_auc']:.3f}  ROC-AUC {test['roc_auc']:.3f}  "
            f"top-decile capture {test['top_decile_capture']:.1%}  Brier {test['brier']:.4f}"
        )

# COMMAND ----------

# MAGIC %md ## Champion: per-year results, calibration, feature importance

# COMMAND ----------

model, p_te = final_models[champion]
by_year = []
for yr in sorted(year_te.unique()):
    mask = (year_te == yr).values
    if y_te[mask].nunique() == 2:
        e = evaluate(y_te[mask], p_te[mask])
        by_year.append({"year": int(yr), **{k: round(v, 4) for k, v in e.items()}})
by_year = pd.DataFrame(by_year)
display(by_year)

# Calibration: predicted vs observed distress rate in 10 equal-size groups
frac_pos, mean_pred = calibration_curve(y_te, p_te, n_bins=10, strategy="quantile")
fig, ax = plt.subplots(figsize=(5, 5))
ax.plot(
    [0, max(mean_pred.max(), frac_pos.max())],
    [0, max(mean_pred.max(), frac_pos.max())],
    "--",
    color="grey",
    label="perfect calibration",
)
ax.plot(mean_pred, frac_pos, "o-", color="#1F3B63", label=champion)
ax.set_xlabel("Predicted probability")
ax.set_ylabel("Observed distress rate")
ax.set_title("Calibration, test 2020–2024")
ax.legend()
fig.tight_layout()

# Permutation importance on a test sample: how much PR-AUC drops when a feature is shuffled
sample = X_te.sample(n=min(40_000, len(X_te)), random_state=SEED)
imp = permutation_importance(
    model,
    sample,
    y_te.loc[sample.index],
    scoring="average_precision",
    n_repeats=3,
    random_state=SEED,
    n_jobs=-1,
)
importance = pd.DataFrame(
    {"feature": FEATURES, "importance": imp.importances_mean, "std": imp.importances_std}
).sort_values("importance", ascending=False)
display(importance.head(15))

# COMMAND ----------

# MAGIC %md ## 3. Band cut-offs from out-of-sample predictions, 2001–2019 only

# COMMAND ----------


def walk_forward(make, years):
    """Predict each year with a model fitted on years <= year-2, whose labels were all known
    before that year began. Uses only 2001–2019 rows."""
    parts = []
    for yr in years:
        fit_rows = pdf["year"].between(PERIODS["train"][0], yr - 2)
        rows = pdf["year"] == yr
        m = make().fit(pdf.loc[fit_rows, FEATURES], y_all[fit_rows])
        parts.append(
            pd.DataFrame(
                {"year": yr, "p": m.predict_proba(pdf.loc[rows, FEATURES])[:, 1], "y": y_all[rows].values}
            )
        )
        print(f"walk-forward {yr}: fitted on {int(fit_rows.sum()):,} rows, predicted {int(rows.sum()):,}")
    return pd.concat(parts, ignore_index=True)


def within_band_cutoff(p, y, upper, target, min_cases=30):
    """Lowest cut-off c below `upper` (None = no upper limit) such that the rows with
    c <= p < upper have a distress rate >= target. Bands are as wide as their meaning allows."""
    order = np.argsort(-p)
    ps, ys = p[order], y[order]
    start = 0 if upper is None else int(np.searchsorted(-ps, -upper, side="right"))
    n = np.arange(1, len(ps) - start + 1)
    rate = np.cumsum(ys[start:]) / n
    ok = np.where((rate >= target) & (n >= min_cases))[0]
    if not len(ok):
        return None
    cut = float(ps[start + ok.max()])
    sel = (p >= cut) & (p < upper if upper is not None else True)
    return {
        "cutoff": cut,
        "target_rate": target,
        "band_rate": round(float(y[sel].mean()), 4),
        "cases": int(sel.sum()),
        "positives": int(y[sel].sum()),
    }


oos = walk_forward(MODELS[champion], range(WALK_FORWARD_FIRST_YEAR, PERIODS["train"][1] + 1))
oos_p, oos_y = oos["p"].to_numpy(), oos["y"].to_numpy()
oos_base = float(oos_y.mean())
spark.createDataFrame(oos).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
    f"{ML}.band_oos_predictions"
)  # kept so the cut-offs can be re-derived
targets = dict(BAND_TARGETS)
band_cutoffs, upper = {}, None
for band in ("High", "Elevated", "Watch"):
    band_cutoffs[band] = within_band_cutoff(oos_p, oos_y, upper, targets[band])
    upper = band_cutoffs[band]["cutoff"] if band_cutoffs[band] else upper
low = oos_p < band_cutoffs["Watch"]["cutoff"] if band_cutoffs["Watch"] else np.ones(len(oos_p), bool)
dq_check(T, "Low band distress rate below the Watch target (10%)", int(oos_y[low].mean() >= targets["Watch"]))
dq_check(
    T, "every band has a cut-off meeting its precision target", sum(v is None for v in band_cutoffs.values())
)
dq_check(
    T,
    "band cut-offs ordered High > Elevated > Watch",
    int(
        not (
            band_cutoffs["High"]["cutoff"]
            > band_cutoffs["Elevated"]["cutoff"]
            > band_cutoffs["Watch"]["cutoff"]
        )
    ),
)
BAND_ORDER = ["High", "Elevated", "Watch"]


def band_of(p):
    out = np.full(len(p), "Low", dtype=object)
    for band in reversed(BAND_ORDER):  # Watch first, High last, so the highest band wins
        out[p >= band_cutoffs[band]["cutoff"]] = band
    return out


# Precision inside each band (not cumulative) on the out-of-sample years
oos["band"] = band_of(oos_p)
band_oos = (
    oos.groupby("band")["y"]
    .agg(cases="size", positives="sum", observed_rate="mean")
    .reindex(BAND_ORDER + ["Low"])
    .reset_index()
)
display(band_oos)
print("Band cut-offs:", json.dumps(band_cutoffs), f"base rate {oos_base:.4f}")

# COMMAND ----------

# MAGIC %md ## 4. Challenger: stricter label (no "two net losses" condition)

# COMMAND ----------

strict_train = y_strict[pdf["year"].between(*PERIODS["train"])]
strict_test = y_strict[pdf["year"].between(*PERIODS["test"])]
with mlflow.start_run(
    run_name="challenger_strict_label", nested=True, tags={"role": "challenger", "label": "strict"}
) as ch_run:
    challenger = MODELS[champion]().fit(X_tr, strict_train)
    p_ch = challenger.predict_proba(X_te)[:, 1]
    ch_own = evaluate(strict_test, p_ch)
    ch_v5label = evaluate(y_te, p_ch)
    mlflow.log_params(
        {
            "label": "failure within 12m, or Tier1<5% or NCL>3% in t+1..t+4",
            "model": champion,
            "n_features": len(FEATURES),
        }
    )
    mlflow.log_metrics({f"test_{k}": v for k, v in ch_own.items()})
    mlflow.log_metrics({f"test_on_main_label_{k}": v for k, v in ch_v5label.items()})
    mlflow.sklearn.log_model(
        sk_model=challenger,
        **{log_model_kwargs(): "model"},
        signature=infer_signature(X_tr.head(200), challenger.predict_proba(X_tr.head(200))),
    )
    challenger_run_id = ch_run.info.run_id
print("challenger (own label)", {k: round(v, 4) for k, v in ch_own.items()})

# COMMAND ----------

# MAGIC %md ## 5. v4 on exactly the same test rows (v4 used total risk-based capital as FDIC reports it)

# COMMAND ----------

try:
    old_version = MlflowClient().get_model_version_by_alias(MODEL_NAME, "champion").version
    old_model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@champion")
    X_old = pdf.loc[X_te.index].assign(total_risk_based_capital_pct=pdf.loc[X_te.index, "raw_total_rbc_pct"])
    p_old = old_model.predict_proba(X_old[list(old_model.feature_names_in_)].astype("float64"))[:, 1]
    old_eval = evaluate(y_te, p_old)
except Exception as exc:  # first ever run, or the old model can't be loaded
    print("previous champion not compared:", str(exc)[:300])
    old_version, p_old, old_eval = None, None, None


def calibration_table(yv, p, bins=10):
    d = pd.DataFrame({"y": np.asarray(yv), "p": p})
    d["decile"] = pd.qcut(d["p"].rank(method="first"), bins, labels=False) + 1
    return (
        d.groupby("decile")
        .agg(mean_pred=("p", "mean"), observed=("y", "mean"), rows=("y", "size"))
        .round(4)
        .reset_index()
    )


comparison = {
    "new_model": {k: round(v, 4) for k, v in results[champion]["test"].items()},
    "challenger_on_main_label": {k: round(v, 4) for k, v in ch_v5label.items()},
    "challenger_on_own_label": {k: round(v, 4) for k, v in ch_own.items()},
}
calibration = {
    "new_model": calibration_table(y_te, p_te).to_dict("records"),
    "challenger_own_label": calibration_table(strict_test, p_ch).to_dict("records"),
}
if old_eval:
    comparison[f"v{old_version}_same_rows"] = {k: round(v, 4) for k, v in old_eval.items()}
    calibration[f"v{old_version}"] = calibration_table(y_te, p_old).to_dict("records")
display(pd.DataFrame(comparison).T)

# Band counts by year: 2003–2019 out-of-sample (walk-forward), 2020–2024 test predictions of the
# new model. 2001–2002 have no earlier years to learn from, so no honest prediction exists.
test_bands = pd.DataFrame({"year": year_te.values, "band": band_of(p_te), "y": y_te.values})
band_years = pd.concat(
    [
        oos[["year", "band", "y"]].assign(source="out-of-sample 2001-2019 walk-forward"),
        test_bands.assign(source="test 2020-2024"),
    ]
)
band_counts = (
    band_years.pivot_table(index=["year", "source"], columns="band", values="y", aggfunc="size", fill_value=0)
    .reindex(columns=BAND_ORDER + ["Low"], fill_value=0)
    .reset_index()
)
band_rates = (
    band_years.pivot_table(index="year", columns="band", values="y", aggfunc="mean")
    .reindex(columns=BAND_ORDER + ["Low"])
    .round(4)
    .reset_index()
)
display(band_counts)
test_band_summary = (
    test_bands.groupby("band")["y"]
    .agg(cases="size", positives="sum", observed_rate="mean")
    .reindex(BAND_ORDER + ["Low"])
    .round(4)
    .reset_index()
)
display(test_band_summary)

# COMMAND ----------

# MAGIC %md ## 6. Log and register the new model in Unity Catalog

# COMMAND ----------

# Stop before registering anything if an error-level check failed
dq_summary = dq_finish()
band_thresholds = {
    "targets": {k: round(v, 4) for k, v in targets.items()},
    "cutoffs": band_cutoffs,
    "base_rate_2003_2019_oos": round(oos_base, 4),
    "method": "within-band distress rate: High >= 50%, Elevated 25-50%, Watch 10-25%, Low < 10%; "
    "each cut-off is the lowest one keeping its band's rate at the target; "
    "walk-forward out-of-sample predictions 2003-2019 (fit on years <= year-2)",
}

with mlflow.start_run(run_id=results[champion]["run_id"], nested=True):
    mlflow.log_figure(fig, "calibration_test.png")
    mlflow.log_dict(band_thresholds, "band_thresholds.json")
    mlflow.log_dict(
        {
            "comparison_test_2020_2024": comparison,
            "calibration_deciles": calibration,
            "exclusions_2001_2019": exclusions_2001_2019,
            "challenger_run_id": challenger_run_id,
        },
        "comparison.json",
    )
    mlflow.log_table(band_counts, "band_counts_by_year.json")
    mlflow.log_table(band_oos, "band_precision_oos_2003_2019.json")
    mlflow.log_metrics({f"band_cutoff_{b.lower()}": c["cutoff"] for b, c in band_cutoffs.items()})
    mlflow.log_table(importance, "feature_importance.json")
    mlflow.log_table(by_year, "test_metrics_by_year.json")
    mlflow.log_dict({"features": FEATURES}, "features.json")
    import sys

    import sklearn

    mlflow.log_params({"sklearn_version": sklearn.__version__, "python_version": sys.version.split()[0]})
    example = X_tr.head(200)
    signature = infer_signature(example, model.predict_proba(example))
    info = mlflow.sklearn.log_model(
        sk_model=model,
        **{log_model_kwargs(): "model"},
        signature=signature,
        input_example=example.head(5),
        pyfunc_predict_fn="predict_proba",
        registered_model_name=MODEL_NAME,
    )
version = info.registered_model_version
client = MlflowClient()
# 05_score reads the band cut-offs from these tags, so each version carries its own bands
for band, c in band_cutoffs.items():
    client.set_model_version_tag(MODEL_NAME, version, f"band_cutoff_{band.lower()}", f"{c['cutoff']:.6f}")
client.set_model_version_tag(
    MODEL_NAME, version, "serverless_environment", os.environ.get("DATABRICKS_RUNTIME_VERSION", "unknown")
)
client.set_model_version_tag(MODEL_NAME, version, "test_period", f"{PERIODS['test'][0]}-{PERIODS['test'][1]}")
client.set_model_version_tag(MODEL_NAME, version, "challenger_run_id", challenger_run_id)
client.set_registered_model_alias(MODEL_NAME, "candidate", version)
if PROMOTE:
    client.set_registered_model_alias(MODEL_NAME, "champion", version)
mlflow.end_run()
print(
    f"Registered {MODEL_NAME} version {version} as @candidate"
    + (" and @champion" if PROMOTE else "; @champion unchanged (promote=false)")
)

# Keep a queryable copy of the metrics next to the data
metric_rows = [
    (RUN_TS, name, split, k, float(v))
    for name, r in results.items()
    for split in ("validation", "test", "train")
    for k, v in r[split].items()
]
metric_rows += [(RUN_TS, "challenger_strict_label", "test", k, float(v)) for k, v in ch_own.items()]
if old_eval:
    metric_rows += [(RUN_TS, f"v{old_version}_same_rows", "test", k, float(v)) for k, v in old_eval.items()]
(
    spark.createDataFrame(
        metric_rows, "run_ts timestamp, model string, split string, metric string, value double"
    )
    .write.mode("append")
    .saveAsTable(f"{ML}.model_metrics")
)

# COMMAND ----------

summary = {
    "model_rows": len(pdf),
    "champion": champion,
    "registered": f"{MODEL_NAME} v{version}",
    "promoted": PROMOTE,
    "exclusions_2001_2019": exclusions_2001_2019,
    "band_thresholds": band_thresholds,
    "band_precision_oos_2003_2019": band_oos.round(4).to_dict("records"),
    "band_test_2020_2024": test_band_summary.to_dict("records"),
    "band_counts_by_year": band_counts.to_dict("records"),
    "comparison_test_2020_2024": comparison,
    "calibration_deciles": calibration,
    "metrics": {
        n: {s: {k: round(v, 4) for k, v in r[s].items()} for s in ("validation", "test")}
        for n, r in results.items()
    },
    "top_features": importance.head(8)[["feature", "importance"]].round(4).to_dict("records"),
    "test_by_year": by_year[["year", "rows", "positives", "roc_auc", "pr_auc", "top_decile_capture"]].to_dict(
        "records"
    ),
    "data_quality": dq_summary,
}
print(json.dumps(summary, indent=2, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
