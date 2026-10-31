# Databricks notebook source
# MAGIC %md
# MAGIC # table_checksums — are two copies of the silver tables identical? (D-02)
# MAGIC
# MAGIC For each silver table: row count, distinct keys, and an order-independent checksum of every
# MAGIC column except the load times (`table_checksum` in `_silver_merge`). With one schema it prints
# MAGIC them; with two (`schemas=silver,silver_fullcheck`) it compares them table by table and fails if
# MAGIC any differ. Read-only.
# MAGIC
# MAGIC Dev check of PR 3: the incremental `silver` tables must equal a full rebuild written by
# MAGIC `02_silver` with `silver_mode=full, silver_schema=silver_fullcheck`.

# COMMAND ----------

# MAGIC %run ../_common

# COMMAND ----------

# MAGIC %run ../_silver_merge

# COMMAND ----------

KEYS = {
    "fdic_financials": ["cert", "report_date"],
    "fdic_institutions": ["cert"],
    "fdic_failures": ["failure_id"],
    "company_bank_map": ["company"],
    "cfpb_complaints": ["complaint_id"],
}
dbutils.widgets.text("schemas", "silver", "One schema, or two to compare (comma-separated)")
schemas = [s.strip() for s in dbutils.widgets.get("schemas").split(",") if s.strip()]
if not 1 <= len(schemas) <= 2:
    raise ValueError("schemas: give one schema, or two to compare")

result = {s: {t: table_checksum(f"{CATALOG}.{s}.{t}", keys) for t, keys in KEYS.items()} for s in schemas}
for s, tables in result.items():
    for t, c in tables.items():
        print(f"{s}.{t}: {c['rows']:,} rows, {c['distinct_keys']:,} keys, checksum {c['checksum']}")

summary = {"schemas": schemas, "tables": result}
if len(schemas) == 2:
    a, b = schemas
    differ = {
        t: {
            k: (result[a][t][k], result[b][t][k])
            for k in ("rows", "distinct_keys", "checksum", "columns")
            if result[a][t][k] != result[b][t][k]
        }
        for t in KEYS
    }
    differ = {t: d for t, d in differ.items() if d}
    summary["identical"] = not differ
    summary["differences"] = differ
    print("IDENTICAL" if not differ else f"DIFFERENT: {json.dumps(differ, indent=1)}")
    if differ:
        raise AssertionError(f"{a} and {b} differ in: {sorted(differ)}")
dbutils.notebook.exit(json.dumps(summary))
