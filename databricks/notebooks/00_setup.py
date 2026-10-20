# Databricks notebook source
# MAGIC %md
# MAGIC # 00_setup — Unity Catalog objects for the Bank Health platform
# MAGIC
# MAGIC Run once (safe to re-run: everything is `IF NOT EXISTS`). Creates:
# MAGIC
# MAGIC | Object | Purpose |
# MAGIC |---|---|
# MAGIC | catalog `bank_health` | top-level container for the project |
# MAGIC | schema `bronze` | raw data as delivered (+ load metadata) |
# MAGIC | schema `silver` | cleaned, typed, de-duplicated, CFPB companies matched to FDIC banks |
# MAGIC | schema `gold` | one row per bank per quarter, ready for analysis and the model |
# MAGIC | schema `ml` | model features, registered model, risk scores |
# MAGIC | volume `bronze.raw_files` | files uploaded by Airflow (FDIC JSON Lines, CFPB CSV) |
# MAGIC | volume `gold.exports` | gold tables written as Parquet for the Snowflake load |
# MAGIC
# MAGIC **Catalog name:** `bank_health` by default. If Free Edition refuses to create a new catalog,
# MAGIC set the `catalog` widget at the top to `workspace` (the built-in catalog), re-run, and put
# MAGIC `DATABRICKS_CATALOG=workspace` in `.env`.

# COMMAND ----------

dbutils.widgets.text("catalog", "bank_health", "Catalog")
CATALOG = dbutils.widgets.get("catalog").strip()
print(f"Using catalog: {CATALOG}")

# COMMAND ----------

# MAGIC %md ## 1. Catalog

# COMMAND ----------

existing = {r.catalog for r in spark.sql("SHOW CATALOGS").collect()}
if CATALOG in existing:
    print(f"Catalog {CATALOG} already exists")
else:
    try:
        spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG} COMMENT 'Bank Health Data Platform'")
        print(f"Created catalog {CATALOG}")
    except Exception as exc:
        raise RuntimeError(
            f"Could not create catalog '{CATALOG}' ({type(exc).__name__}: {str(exc)[:300]}).\n"
            "Free Edition may not allow new catalogs. Set the 'catalog' widget to 'workspace', "
            "re-run this notebook, and add DATABRICKS_CATALOG=workspace to .env."
        ) from None

# COMMAND ----------

# MAGIC %md ## 2. Schemas (one per layer)

# COMMAND ----------

SCHEMAS = {
    "bronze": "Raw data as delivered, plus load metadata",
    "silver": "Cleaned, typed, de-duplicated; CFPB companies matched to FDIC CERT",
    "gold": "One row per bank per quarter: ratios and complaint counts",
    "ml": "Model features, registered model and risk scores",
}
for name, comment in SCHEMAS.items():
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{name} COMMENT '{comment}'")
    print(f"schema {CATALOG}.{name} ready")

# COMMAND ----------

# MAGIC %md ## 3. Volumes (governed folders for files)

# COMMAND ----------

VOLUMES = {
    "bronze.raw_files": "Files uploaded by Airflow: FDIC JSON Lines and CFPB CSV (gzipped)",
    "gold.exports": "Gold tables as Parquet, pulled by Airflow into Snowflake RAW",
}
for name, comment in VOLUMES.items():
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{name} COMMENT '{comment}'")
    print(f"volume {CATALOG}.{name} ready")

# Folder layout that Airflow's uploads mirror (data/raw/... on the PC)
RAW = f"/Volumes/{CATALOG}/bronze/raw_files"
for sub in ("fdic/financials", "fdic/institutions", "fdic/failures", "cfpb/complaints"):
    dbutils.fs.mkdirs(f"{RAW}/{sub}")

# COMMAND ----------

# MAGIC %md ## 4. Check everything

# COMMAND ----------

display(spark.sql(f"SHOW SCHEMAS IN {CATALOG}"))

# COMMAND ----------

for schema in ("bronze", "gold"):
    display(spark.sql(f"SHOW VOLUMES IN {CATALOG}.{schema}"))

# COMMAND ----------

# Round-trip test: write a small file to the volume, read it back, delete it.
probe = f"{RAW}/_setup_probe.txt"
dbutils.fs.put(probe, "ok", overwrite=True)
assert dbutils.fs.head(probe) == "ok"
dbutils.fs.rm(probe)

print("Folders in raw_files:", sorted(f.name for f in dbutils.fs.ls(RAW)))
print(f"\nSetup complete. Airflow uploads go to {RAW}")
