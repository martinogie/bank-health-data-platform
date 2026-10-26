# Databricks notebook source
# MAGIC %md
# MAGIC # _common — shared settings and data-quality helpers
# MAGIC Loaded by the other notebooks with `%run ./_common`. Not meant to be run on its own.

# COMMAND ----------

import datetime
import json  # used by the notebooks that `%run ./_common`

from pyspark.sql import Window
from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "bank_health", "Catalog")
CATALOG = dbutils.widgets.get("catalog").strip()

RAW = f"/Volumes/{CATALOG}/bronze/raw_files"
CHECKPOINTS = f"/Volumes/{CATALOG}/bronze/checkpoints"
EXPORTS = f"/Volumes/{CATALOG}/gold/exports"
BRONZE, SILVER, GOLD, ML = (f"{CATALOG}.{s}" for s in ("bronze", "silver", "gold", "ml"))

RUN_TS = datetime.datetime.now(datetime.UTC)
print(f"catalog={CATALOG}  run_ts={RUN_TS:%Y-%m-%d %H:%M:%S} UTC")

# Serverless environment the model is trained and scored in (ENVIRONMENT_VERSION in
# ingestion/databricks_jobs.py). DATABRICKS_RUNTIME_VERSION looks like "client.2.5".
EXPECTED_ENVIRONMENT = "2"


def require_pinned_environment():
    """Stop if this run is not in the pinned environment: a model pickled in one environment
    may not load (or may behave differently) in another."""
    import os

    runtime = os.environ.get("DATABRICKS_RUNTIME_VERSION", "")
    if runtime.split(".")[:2] != ["client", EXPECTED_ENVIRONMENT]:
        raise RuntimeError(
            f"running in serverless environment {runtime!r}, expected version "
            f"{EXPECTED_ENVIRONMENT}; run via the job or scripts/databricks_run.py"
        )
    print(f"serverless environment {runtime} (pinned to version {EXPECTED_ENVIRONMENT})")


# COMMAND ----------


def latest_per_key(df, keys, order_cols):
    """Keep one row per key: the first by `order_cols` descending (e.g. newest load)."""
    w = Window.partitionBy(*keys).orderBy(*[F.col(c).desc_nulls_last() for c in order_cols])
    return df.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")


def clean_str(col_name):
    """Trim, and turn '', 'None' and 'N/A' (used as blanks in the raw files) into NULL."""
    c = F.trim(F.col(col_name))
    return F.when(c.isin("", "None", "N/A"), None).otherwise(c)


# COMMAND ----------

# --- Data-quality checks: results go to silver.dq_results; "error" level stops the run.
_dq_results = []


def dq_check(table, check, failed_rows, level="error", detail=""):
    passed = failed_rows == 0
    _dq_results.append((RUN_TS, NOTEBOOK, table, check, level, int(failed_rows), passed, detail))
    status = "PASS" if passed else level.upper()
    print(f"[{status:5}] {table}: {check} (failed rows: {failed_rows}) {detail}")


def dq_unique(df, table, keys):
    dupes = df.groupBy(*keys).count().filter("count > 1").count()
    dq_check(table, f"unique({', '.join(keys)})", dupes)


def dq_not_null(df, table, cols):
    for c in cols:
        dq_check(table, f"not_null({c})", df.filter(F.col(c).isNull()).count())


def dq_range(df, table, col_name, low=None, high=None, level="warn"):
    """Values outside [low, high] (NULLs ignored). Warn by default: outliers are real in bank data."""
    cond = F.lit(False)
    if low is not None:
        cond = cond | (F.col(col_name) < low)
    if high is not None:
        cond = cond | (F.col(col_name) > high)
    dq_check(table, f"range({col_name} in [{low}, {high}])", df.filter(cond).count(), level)


def dq_finish():
    """Save results; raise if any error-level check failed."""
    schema = (
        "run_ts timestamp, notebook string, table_name string, check_name string, "
        "level string, failed_rows long, passed boolean, detail string"
    )
    spark.createDataFrame(_dq_results, schema).write.mode("append").saveAsTable(f"{SILVER}.dq_results")
    errors = [r for r in _dq_results if r[4] == "error" and not r[6]]
    warns = [r for r in _dq_results if r[4] == "warn" and not r[6]]
    print(f"\nData quality: {len(_dq_results)} checks, {len(errors)} errors, {len(warns)} warnings")
    if errors:
        raise AssertionError("Data-quality errors: " + "; ".join(f"{r[2]} {r[3]}" for r in errors))
    return {"checks": len(_dq_results), "errors": 0, "warnings": len(warns)}
