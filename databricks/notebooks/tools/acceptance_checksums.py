# Databricks notebook source
# MAGIC %md
# MAGIC # acceptance_checksums — one snapshot of every Databricks layer (D-02 acceptance)
# MAGIC
# MAGIC Read-only. Returns, for each table, its row count, distinct keys and an order-independent
# MAGIC checksum (`table_checksum` in `_silver_merge`), as JSON in the notebook's exit value.
# MAGIC `scripts/acceptance_snapshot.py` runs it after a pipeline run and compares two snapshots.
# MAGIC
# MAGIC - **Bronze** is append-only, so it grows on every re-run by design: only its **distinct keys**
# MAGIC   are compared (row counts and checksums are shown, not compared).
# MAGIC - **Silver, gold, risk scores** must be identical. Left out of the checksum: the lineage and
# MAGIC   load-time columns, which say where and when a row was loaded, not what it says
# MAGIC   (`_source_file`, `_ingested_at`, `_file_modified_at`, `scored_at`). A re-run names its CFPB
# MAGIC   files by different date windows, so `_source_file` changes even when the data doesn't.

# COMMAND ----------

# MAGIC %run ../_common

# COMMAND ----------

# MAGIC %run ../_silver_merge

# COMMAND ----------

LINEAGE = ("_source_file", "_ingested_at", "_file_modified_at", "scored_at")
TABLES = {
    # table: (keys, compare: "keys" = distinct keys only, "all" = rows, keys and checksum)
    f"{BRONZE}.fdic_financials": (["cert", "repdte"], "keys"),
    f"{BRONZE}.fdic_institutions": (["cert"], "keys"),
    f"{BRONZE}.fdic_failures": (["id"], "keys"),
    f"{BRONZE}.cfpb_complaints": (["complaint_id"], "keys"),
    f"{SILVER}.fdic_financials": (["cert", "report_date"], "all"),
    f"{SILVER}.fdic_institutions": (["cert"], "all"),
    f"{SILVER}.fdic_failures": (["failure_id"], "all"),
    f"{SILVER}.company_bank_map": (["company"], "all"),
    f"{SILVER}.cfpb_complaints": (["complaint_id"], "all"),
    f"{GOLD}.bank_quarter": (["cert", "report_date"], "all"),
    f"{ML}.risk_scores": (["cert"], "all"),
}

out = {}
for table, (keys, compare) in TABLES.items():
    if not spark.catalog.tableExists(table):
        out[table] = {"missing": True, "compare": compare}
        continue
    c = table_checksum(table, keys, exclude=LINEAGE)
    out[table] = {**c, "compare": compare}
    print(f"{table}: {c['rows']:,} rows, {c['distinct_keys']:,} keys, checksum {c['checksum']} ({compare})")

dbutils.notebook.exit(json.dumps({"catalog": CATALOG, "tables": out}))
