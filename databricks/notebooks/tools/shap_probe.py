# Databricks notebook source
# MAGIC %md
# MAGIC # shap_probe — does a pinned shap version work here, on the v5 model? (M-05, PR 7 step 1)
# MAGIC
# MAGIC Read-only. Installs shap next to the job's pinned mlflow and scikit-learn (numpy stays the
# MAGIC environment's), loads `@champion` the way `05_score` does, and explains the latest quarter:
# MAGIC
# MAGIC - versions after install (numpy, scikit-learn, shap, numba) — numpy must not change;
# MAGIC - `TreeExplainer` on the gradient-boosting model, fed the same inputs as scoring (NaN kept);
# MAGIC - additivity: base value + sum of SHAP values = the model's log-odds, for every bank,
# MAGIC   including banks with missing values (max error must be tiny);
# MAGIC - how long it takes. Writes nothing.

# COMMAND ----------

# MAGIC %pip install --quiet "mlflow==2.22.5" "scikit-learn==1.5.2" "shap==0.46.0" "numba==0.60.0" "llvmlite==0.43.0"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ../_common

# COMMAND ----------

# MAGIC %run ../_features

# COMMAND ----------

# MAGIC %run ../_scoring_rules

# COMMAND ----------

import sys
import time

import mlflow
import numba
import numpy as np
import shap
import sklearn

mlflow.set_registry_uri("databricks-uc")
model = mlflow.sklearn.load_model(f"models:/{CHAMPION_MODEL}@champion")
features = list(model.feature_names_in_)
booster = model[-1] if hasattr(model, "steps") else model  # v5: a plain HistGradientBoostingClassifier

frame = build_model_frame()
latest = frame.agg(F.max("report_date")).first()[0]
cur = frame.filter(F.col("report_date") == latest).toPandas()
scored = ~cur["is_foreign_office"] & ~cur["is_new_bank"]
X = prepare_features(cur[scored], features)
Z = model[:-1].transform(X) if hasattr(model, "steps") else X  # HGB takes missing values as they are

t0 = time.time()
explainer = shap.TreeExplainer(booster)
sv = explainer.shap_values(Z)
seconds = time.time() - t0
sv = sv[1] if isinstance(sv, list) else sv
base = float(np.ravel(explainer.expected_value)[-1])
logodds = model.decision_function(X)
err = np.abs(base + sv.sum(axis=1) - logodds)
has_missing = X.isna().any(axis=1).to_numpy()

result = {
    "python": sys.version.split()[0],
    "numpy": np.__version__,
    "scikit_learn": sklearn.__version__,
    "shap": shap.__version__,
    "numba": numba.__version__,
    "model": type(booster).__name__,
    "features": len(features),
    "banks": int(len(X)),
    "banks_with_missing_values": int(has_missing.sum()),
    "seconds": round(seconds, 1),
    "additivity_max_error": float(err.max()),
    "additivity_max_error_missing_rows": float(err[has_missing].max()) if has_missing.any() else None,
}
print(result)
dbutils.notebook.exit(json.dumps(result))
