# Databricks notebook source
# MAGIC %md
# MAGIC # 01_bronze — raw files to Delta tables
# MAGIC
# MAGIC Reads the files Airflow uploaded to `bronze.raw_files` with **Auto Loader**, which remembers
# MAGIC (in a checkpoint) which files it has already ingested, so each run only reads new or
# MAGIC re-uploaded files.
# MAGIC
# MAGIC Bronze keeps the data **as delivered**: every value stays text, nothing is filtered or
# MAGIC de-duplicated. Only column names are made Delta-friendly (`Date received` → `date_received`).
# MAGIC Each row gets lineage columns: `_source_file`, `_file_modified_at`, `_ingested_at`.
# MAGIC
# MAGIC | Source folder | Bronze table |
# MAGIC |---|---|
# MAGIC | `fdic/financials/*.jsonl.gz` | `bronze.fdic_financials` |
# MAGIC | `fdic/institutions/*.jsonl.gz` | `bronze.fdic_institutions` |
# MAGIC | `fdic/failures/*.jsonl.gz` | `bronze.fdic_failures` |
# MAGIC | `cfpb/complaints/*.csv.gz` | `bronze.cfpb_complaints` |

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

import re

NOTEBOOK = "01_bronze"
spark.sql(f"CREATE VOLUME IF NOT EXISTS {BRONZE}.checkpoints "
          "COMMENT 'Auto Loader checkpoints and inferred schemas'")


def snake(name: str) -> str:
    """'Timely response?' -> 'timely_response', 'CERT' -> 'cert'."""
    return re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_")


def ingest(subdir: str, fmt: str, table: str, **reader_options) -> dict:
    """Incrementally load new files from RAW/<subdir> into BRONZE.<table>."""
    target = f"{BRONZE}.{table}"
    checkpoint = f"{CHECKPOINTS}/{table}"
    reader = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", fmt)
        .option("cloudFiles.schemaLocation", f"{checkpoint}/_schema")
        .option("cloudFiles.inferColumnTypes", "false")   # keep every value as text
        .option("cloudFiles.allowOverwrites", "true")     # re-read files that were re-uploaded
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .option("pathGlobFilter", "*.gz")                 # skip any half-written .tmp files
    )
    for key, value in reader_options.items():
        reader = reader.option(key, value)
    df = reader.load(f"{RAW}/{subdir}/")
    df = df.select(
        *[F.col(f"`{c}`").alias(c if c.startswith("_") else snake(c)) for c in df.columns],
        F.col("_metadata.file_path").alias("_source_file"),
        F.col("_metadata.file_modification_time").alias("_file_modified_at"),
        F.current_timestamp().alias("_ingested_at"),
    )
    query = (
        df.writeStream.option("checkpointLocation", checkpoint)
        .option("mergeSchema", "true")
        .trigger(availableNow=True)                       # process what's there, then stop
        .toTable(target)
    )
    query.awaitTermination()
    # numInputRows can be None for a batch with nothing new, so treat missing as 0
    new_rows = sum(p.get("numInputRows") or 0 for p in query.recentProgress)
    total = spark.table(target).count()
    files = spark.table(target).select("_source_file").distinct().count()
    print(f"{target}: +{new_rows} rows this run, {total} total from {files} files")
    return {"new_rows": new_rows, "total_rows": total, "files": files}


def ingest_with_retry(*args, **kwargs) -> dict:
    """Auto Loader stops once when it sees a new column, by design; the retry picks it up."""
    try:
        return ingest(*args, **kwargs)
    except Exception as exc:
        if "UnknownFieldException" not in str(exc) and "UNKNOWN_FIELD" not in str(exc):
            raise
        print("New column detected, schema updated; re-running once")
        return ingest(*args, **kwargs)

# COMMAND ----------

summary = {
    "fdic_financials": ingest_with_retry("fdic/financials", "json", "fdic_financials"),
    "fdic_institutions": ingest_with_retry("fdic/institutions", "json", "fdic_institutions"),
    "fdic_failures": ingest_with_retry("fdic/failures", "json", "fdic_failures"),
    "cfpb_complaints": ingest_with_retry(
        "cfpb/complaints", "csv", "cfpb_complaints",
        header="true", multiLine="true", escape='"',
    ),
}

# COMMAND ----------

display(spark.table(f"{BRONZE}.cfpb_complaints").limit(5))

# COMMAND ----------

dbutils.notebook.exit(json.dumps(summary))
