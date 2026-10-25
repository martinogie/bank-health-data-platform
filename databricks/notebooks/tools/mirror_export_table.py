# Databricks notebook source
# MAGIC %md
# MAGIC # mirror_export_table — fallback export for the PostgreSQL mirror
# MAGIC
# MAGIC Used by `scripts/mirror_to_postgres.py` when a table is too large for the SQL warehouse or it
# MAGIC times out. Writes one table to Parquet in a Unity Catalog volume; the script then downloads the
# MAGIC files with the Files API and loads them into Postgres. The script uploads this notebook to
# MAGIC `/Users/<you>/.bank_health_tools/` and runs it as a one-off serverless job.

# COMMAND ----------

import json

dbutils.widgets.text("table", "")
dbutils.widgets.text("out_dir", "")
table = dbutils.widgets.get("table").strip()
out_dir = dbutils.widgets.get("out_dir").strip()

# Only tables from this project, only into this project's volumes
assert table.count(".") == 2 and table.split(".")[1] in ("bronze", "silver", "gold", "ml"), table
assert out_dir.startswith("/Volumes/") and "/_mirror/" in out_dir, out_dir

df = spark.table(table)
df.write.mode("overwrite").parquet(out_dir)
rows = spark.read.parquet(out_dir).count()
print(f"{table}: {rows:,} rows -> {out_dir}")
dbutils.notebook.exit(json.dumps({"table": table, "rows": rows, "out_dir": out_dir}))
