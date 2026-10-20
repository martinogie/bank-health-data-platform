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
# MAGIC 3. Register the selected model in Unity Catalog as `bank_health.ml.distress_model`
# MAGIC    with alias `champion`.
# MAGIC
# MAGIC Models: logistic regression (baseline) and gradient-boosted trees (scikit-learn
# MAGIC `HistGradientBoostingClassifier`). Metrics: ROC-AUC, PR-AUC, top-decile capture, Brier
# MAGIC score, calibration. *Portfolio model on public data — not a supervisory rating.*

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

# MAGIC %run ./_features

# COMMAND ----------

import inspect
import math

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
MODEL_NAME = f"{CATALOG}.ml.distress_model"
SEED = 42
PERIODS = {
    "selection_train": (2001, 2016),
    "validation": (2017, 2019),
    "train": (2001, 2019),
    "test": (2020, 2024),
}

# COMMAND ----------

# MAGIC %md ## Build features and labels

# COMMAND ----------

frame = build_model_frame()
frame.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{ML}.model_frame")
frame = spark.table(f"{ML}.model_frame")

pdf = frame.filter("is_model_row").select("cert", "report_date", *FEATURES, "distress_next_4q").toPandas()
pdf["year"] = pd.to_datetime(pdf["report_date"]).dt.year
pdf[FEATURES] = pdf[FEATURES].astype("float64")
y_all = pdf["distress_next_4q"].astype(int)


def period(name):
    lo, hi = PERIODS[name]
    mask = pdf["year"].between(lo, hi)
    return pdf.loc[mask, FEATURES], y_all[mask], pdf.loc[mask, "year"]


summary_rows = (pdf.groupby("year")["distress_next_4q"].agg(["count", "mean"])
                .rename(columns={"count": "bank_quarters", "mean": "distress_rate"}))
display(summary_rows.reset_index())
print(f"Model rows: {len(pdf):,}  overall distress rate: {y_all.mean():.3%}")

T = "ml.model_frame"
dq_check(T, "model rows > 100k", int(len(pdf) < 100_000))
dq_check(T, "both classes present in train and test",
         int(any(period(p)[1].nunique() < 2 for p in ("train", "test"))))
dq_unique(frame, T, ["cert", "report_date"])

# COMMAND ----------

# MAGIC %md ## Models and metrics

# COMMAND ----------

def make_logistic():
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", QuantileTransformer(output_distribution="normal", n_quantiles=1000,
                                      subsample=200_000, random_state=SEED)),
        ("model", LogisticRegression(C=1.0, max_iter=2000)),
    ])


def make_gbt():
    return HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=500, max_leaf_nodes=31, min_samples_leaf=200,
        l2_regularization=1.0, early_stopping=True, validation_fraction=0.1,
        n_iter_no_change=30, random_state=SEED,
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
        "top_decile_capture": capture,          # share of all distress cases in the riskiest 10%
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

user = spark.sql("select current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/bank_health_distress_model")
mlflow.set_registry_uri("databricks-uc")

X_sel, y_sel, _ = period("selection_train")
X_val, y_val, _ = period("validation")
X_tr, y_tr, _ = period("train")
X_te, y_te, year_te = period("test")

results = {}
parent = mlflow.start_run(run_name=f"distress_model_{RUN_TS:%Y%m%d_%H%M}")
mlflow.log_params({
    "label": "distress (NCL>3% | Tier1<5% | 2 consecutive net losses) or failure within 4 quarters",
    "ncl_threshold_pct": NCL_THRESHOLD, "tier1_threshold_pct": TIER1_THRESHOLD,
    "periods": str(PERIODS), "n_features": len(FEATURES), "model_rows": len(pdf),
})
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
        print(f"{name:20s} test PR-AUC {test['pr_auc']:.3f}  ROC-AUC {test['roc_auc']:.3f}  "
              f"top-decile capture {test['top_decile_capture']:.1%}  Brier {test['brier']:.4f}")

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
ax.plot([0, max(mean_pred.max(), frac_pos.max())], [0, max(mean_pred.max(), frac_pos.max())],
        "--", color="grey", label="perfect calibration")
ax.plot(mean_pred, frac_pos, "o-", color="#1F3B63", label=champion)
ax.set_xlabel("Predicted probability"); ax.set_ylabel("Observed distress rate")
ax.set_title("Calibration, test 2020–2024"); ax.legend()
fig.tight_layout()

# Permutation importance on a test sample: how much PR-AUC drops when a feature is shuffled
sample = X_te.sample(n=min(40_000, len(X_te)), random_state=SEED)
imp = permutation_importance(model, sample, y_te.loc[sample.index], scoring="average_precision",
                             n_repeats=3, random_state=SEED, n_jobs=-1)
importance = (pd.DataFrame({"feature": FEATURES, "importance": imp.importances_mean,
                            "std": imp.importances_std})
              .sort_values("importance", ascending=False))
display(importance.head(15))

# COMMAND ----------

# MAGIC %md ## 3. Log and register the champion in Unity Catalog

# COMMAND ----------

with mlflow.start_run(run_id=results[champion]["run_id"], nested=True):
    mlflow.log_figure(fig, "calibration_test.png")
    mlflow.log_table(importance, "feature_importance.json")
    mlflow.log_table(by_year, "test_metrics_by_year.json")
    mlflow.log_dict({"features": FEATURES}, "features.json")
    example = X_tr.head(200)
    signature = infer_signature(example, model.predict_proba(example))
    info = mlflow.sklearn.log_model(
        sk_model=model, **{log_model_kwargs(): "model"},
        signature=signature, input_example=example.head(5),
        pyfunc_predict_fn="predict_proba", registered_model_name=MODEL_NAME,
    )
version = info.registered_model_version
MlflowClient().set_registered_model_alias(MODEL_NAME, "champion", version)
mlflow.end_run()
print(f"Registered {MODEL_NAME} version {version} as @champion")

# Keep a queryable copy of the metrics next to the data
metric_rows = [(RUN_TS, name, split, k, float(v))
               for name, r in results.items() for split in ("validation", "test", "train")
               for k, v in r[split].items()]
(spark.createDataFrame(metric_rows, "run_ts timestamp, model string, split string, metric string, value double")
 .write.mode("append").saveAsTable(f"{ML}.model_metrics"))

# COMMAND ----------

dq_summary = dq_finish()
summary = {
    "model_rows": len(pdf),
    "champion": champion,
    "registered": f"{MODEL_NAME} v{version}",
    "metrics": {n: {s: {k: round(v, 4) for k, v in r[s].items()} for s in ("validation", "test")}
                for n, r in results.items()},
    "top_features": importance.head(8)[["feature", "importance"]].round(4).to_dict("records"),
    "test_by_year": by_year[["year", "rows", "positives", "roc_auc", "pr_auc", "top_decile_capture"]].to_dict("records"),
    "data_quality": dq_summary,
}
print(json.dumps(summary, indent=2, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
