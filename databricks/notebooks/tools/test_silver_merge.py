# Databricks notebook source
# MAGIC %md
# MAGIC # test_silver_merge — dev-only checks of the incremental silver load (D-02)
# MAGIC
# MAGIC Builds small synthetic tables in a scratch schema (`<dev catalog>.merge_test`, dropped at the
# MAGIC end), runs the same functions 02_silver uses (`_silver_merge`), and asserts the result row by
# MAGIC row. Any failed check stops the notebook. Refuses to run in prod.
# MAGIC
# MAGIC 1. A full load picks the newest load per key (`_ingested_at`, then `_file_modified_at`).
# MAGIC 2. An incremental MERGE of a later batch (a new key, an amended key) gives exactly what a full
# MAGIC    rebuild from all of bronze gives: same rows, same checksum.
# MAGIC 3. Re-running with nothing new changes nothing (0 inserted, 0 updated, same checksum).
# MAGIC 4. Re-matching complaints touches only rows whose company's match changed.
# MAGIC 5. A changed column set is detected (so 02_silver rebuilds instead of merging).
# MAGIC 6. The checksum ignores row order and load times, and changes when a value changes.

# COMMAND ----------

# MAGIC %run ../_common

# COMMAND ----------

# MAGIC %run ../_silver_merge

# COMMAND ----------

import datetime as dt

if IS_PROD:
    raise RuntimeError("test_silver_merge writes scratch tables: dev only")

SCRATCH = f"{CATALOG}.merge_test"
spark.sql(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE")
spark.sql(f"CREATE SCHEMA {SCRATCH}")
T = f"{SCRATCH}.target"
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "ok": bool(ok), "detail": str(detail)})
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""))


def ts(day, minute=0):
    return dt.datetime(2026, 11, day, 12, minute)


BRONZE_SCHEMA = "k int, v string, _source_file string, _ingested_at timestamp, _file_modified_at timestamp"


def project(df):  # like 02_silver: silver keeps _ingested_at but not _file_modified_at
    return df.select("k", "v", "_source_file", "_ingested_at")


def full_rebuild(bronze, table):
    project(latest_per_key(bronze, ["k"], ["_ingested_at", "_file_modified_at"])).write.mode(
        "overwrite"
    ).option("overwriteSchema", "true").saveAsTable(table)


def rows(table):
    return {r["k"]: r["v"] for r in spark.table(table).collect()}


# COMMAND ----------

# 1. Full load: newest load per key. k=2 has two rows loaded at the same moment: the file modified
#    later wins (the tie-break 02_silver uses).
batch1 = spark.createDataFrame(
    [
        (1, "a", "f1", ts(2), ts(1, 5)),
        (2, "b", "f1", ts(2), ts(1, 5)),
        (2, "b-older-file", "f0", ts(2), ts(1, 1)),
    ],
    BRONZE_SCHEMA,
)
full_rebuild(batch1, T)
check("1 full load keeps the newest load per key", rows(T) == {1: "a", 2: "b"}, rows(T))

# 2. A later batch: k=2 amended (its file is older, but it was loaded later, and the later load
#    wins, as in a full rebuild), k=3 new.
batch2 = spark.createDataFrame(
    [(2, "b-amended", "f2", ts(9), ts(1, 0)), (3, "c", "f2", ts(9), ts(8, 0))],
    BRONZE_SCHEMA,
)
bronze_all = batch1.unionByName(batch2)
batch = project(latest_per_key(newer_than(bronze_all, T), ["k"], ["_ingested_at", "_file_modified_at"]))
stats = merge_newest(T, batch, ["k"])
check(
    "2a merge inserts the new key and updates the amended one", stats == {"inserted": 1, "updated": 1}, stats
)
check("2b merged rows", rows(T) == {1: "a", 2: "b-amended", 3: "c"}, rows(T))
full_rebuild(bronze_all, f"{SCRATCH}.full")
inc, full = table_checksum(T, ["k"]), table_checksum(f"{SCRATCH}.full", ["k"])
check("2c incremental equals a full rebuild (checksum)", inc == full, f"{inc} vs {full}")

# 3. Nothing new: nothing changes.
batch = project(latest_per_key(newer_than(bronze_all, T), ["k"], ["_ingested_at", "_file_modified_at"]))
stats = merge_newest(T, batch, ["k"])
check(
    "3a a re-run with nothing new inserts and updates nothing", stats == {"inserted": 0, "updated": 0}, stats
)
check("3b and leaves the same checksum", table_checksum(T, ["k"]) == inc)

# 4. Re-matching complaints: only rows whose company's match changed are rewritten.
C = f"{SCRATCH}.complaints"
spark.createDataFrame(
    [(1, "BANK A", None, None), (2, "BANK A", None, None), (3, "SHOP B", None, None)],
    "complaint_id bigint, company string, cert int, match_method string",
).write.saveAsTable(C)
map1 = spark.createDataFrame(
    [("BANK A", 100, "bank_name"), ("SHOP B", None, "unmatched")],
    "company string, cert int, match_method string",
)
check("4a first match sets every complaint", rematch(C, map1) == 3)
check("4b the same map again changes nothing", rematch(C, map1) == 0)
map2 = spark.createDataFrame(
    [("BANK A", 100, "bank_name"), ("SHOP B", 200, "manual_override")],
    "company string, cert int, match_method string",
)
updated = rematch(C, map2)
got = {r["complaint_id"]: (r["cert"], r["match_method"]) for r in spark.table(C).collect()}
check("4c a changed match rewrites only that company's complaints", updated == 1, updated)
check(
    "4d and sets the new cert",
    got == {1: (100, "bank_name"), 2: (100, "bank_name"), 3: (200, "manual_override")},
    got,
)

# 5. A changed column set is detected, so 02_silver rebuilds instead of merging.
same = schema_difference(T, project(batch1.limit(0)))
changed = schema_difference(T, project(batch1.limit(0)).withColumn("extra", F.lit(1)))
check("5a same columns: merge", same is None, same)
check("5b a new column: rebuild", changed is not None and "added ['extra']" in changed, changed)
check(
    "5c a missing table: rebuild", schema_difference(f"{SCRATCH}.nope", batch1) == "table does not exist yet"
)

# 6. The checksum ignores row order and load times, and catches a changed value.
spark.table(T).orderBy(F.col("k").desc()).withColumn("_ingested_at", F.current_timestamp()).write.saveAsTable(
    f"{SCRATCH}.shuffled"
)
check(
    "6a row order and load times don't change the checksum",
    table_checksum(f"{SCRATCH}.shuffled", ["k"])["checksum"] == inc["checksum"],
)
spark.table(T).withColumn("v", F.when(F.col("k") == 3, F.lit("c!")).otherwise(F.col("v"))).write.saveAsTable(
    f"{SCRATCH}.changed"
)
check(
    "6b one changed value changes it",
    table_checksum(f"{SCRATCH}.changed", ["k"])["checksum"] != inc["checksum"],
)

# COMMAND ----------

spark.sql(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE")
failed = [c for c in CHECKS if not c["ok"]]
summary = {"checks": len(CHECKS), "passed": len(CHECKS) - len(failed), "failed": failed}
print(json.dumps(summary, indent=2))
if failed:
    raise AssertionError(f"{len(failed)} silver merge check(s) failed: {[c['check'] for c in failed]}")
dbutils.notebook.exit(json.dumps(summary))
