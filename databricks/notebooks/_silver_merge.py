# Databricks notebook source
# MAGIC %md
# MAGIC # _silver_merge — incremental loads for silver (D-02), shared by 02_silver and its dev test
# MAGIC
# MAGIC Loaded with `%run ./_silver_merge` after `./_common`.
# MAGIC
# MAGIC **The rule.** Bronze only ever appends, and the newest load of a key wins (`_ingested_at`,
# MAGIC then `_file_modified_at`). So the newest `_ingested_at` already in a silver table marks how far
# MAGIC that table has caught up: the next batch is every bronze row ingested *after* it, reduced to the
# MAGIC newest load per key, then MERGEd: new keys are inserted, existing keys updated. Every batch row
# MAGIC is newer than any silver row, so the result is the same as rebuilding from all of bronze. A
# MAGIC failed run leaves the mark where it was, and the next run picks the rows up. No extra state.
# MAGIC
# MAGIC A table is rebuilt in full instead when it doesn't exist yet, when its columns differ from what
# MAGIC the code writes (e.g. a new FDIC field mapped), or when the notebook runs with
# MAGIC `silver_mode=full`.

# COMMAND ----------

from delta.tables import DeltaTable

SILVER_MODES = ("incremental", "full")


def schema_difference(target, df):
    """Why `target` can't be merged into incrementally (None if it can): missing, or other columns."""
    if not spark.catalog.tableExists(target):
        return "table does not exist yet"
    have = {f.name: f.dataType.simpleString() for f in spark.table(target).schema}
    want = {f.name: f.dataType.simpleString() for f in df.schema}
    if have == want:
        return None
    added = sorted(set(want) - set(have))
    removed = sorted(set(have) - set(want))
    retyped = sorted(c for c in set(have) & set(want) if have[c] != want[c])
    return f"columns differ (added {added}, removed {removed}, retyped {retyped})"


def newer_than(bronze_df, target, ts_col="_ingested_at"):
    """Bronze rows ingested after the newest one already in `target` (all rows if it is empty)."""
    mark = spark.table(target).agg(F.max(ts_col)).first()[0]
    return bronze_df if mark is None else bronze_df.filter(F.col(ts_col) > F.lit(mark))


def merge_newest(target, batch, keys, keep_on_update=()):
    """MERGE `batch` (one row per key) into `target`: insert new keys; update existing keys.
    `keep_on_update` = target columns an update must not touch (e.g. the matched cert, set later).
    Returns {"inserted": n, "updated": n}. An empty batch writes nothing."""
    if batch.isEmpty():
        return {"inserted": 0, "updated": 0}
    table = DeltaTable.forName(spark, target)
    on = " AND ".join(f"t.`{k}` = s.`{k}`" for k in keys)
    update = {c: f"s.`{c}`" for c in batch.columns if c not in keep_on_update}
    insert = {c: f"s.`{c}`" for c in batch.columns}
    (
        table.alias("t")
        .merge(batch.alias("s"), on)
        # always true by construction (the batch is newer than everything in silver); kept as a guard
        .whenMatchedUpdate(condition="s._ingested_at > t._ingested_at", set=update)
        .whenNotMatchedInsert(values=insert)
        .execute()
    )
    metrics = table.history(1).first()["operationMetrics"]
    return {
        "inserted": int(metrics.get("numTargetRowsInserted", 0)),
        "updated": int(metrics.get("numTargetRowsUpdated", 0)),
    }


def rematch(target, company_map):
    """Set cert and match_method on complaints whose company's match differs from `company_map`
    (one row per company). Only changed rows are rewritten. Returns the number updated."""
    src = company_map.select(
        "company", F.col("cert").alias("m_cert"), F.col("match_method").alias("m_method")
    )
    table = DeltaTable.forName(spark, target)
    (
        table.alias("t")
        .merge(src.alias("s"), "t.company = s.company")
        .whenMatchedUpdate(
            condition="NOT (t.cert <=> s.m_cert) OR NOT (t.match_method <=> s.m_method)",
            set={"cert": "s.m_cert", "match_method": "s.m_method"},
        )
        .execute()
    )
    return int(table.history(1).first()["operationMetrics"].get("numTargetRowsUpdated", 0))


def table_checksum(table, keys, exclude=("_ingested_at", "_file_modified_at")):
    """Row count, distinct keys, and an order-independent checksum of every column except the
    load-time ones: the sum of xxhash64 over each row's columns (sorted by name), as DECIMAL(38,0)
    so it can't overflow. Same data -> same checksum, whatever the row order or file layout."""
    df = spark.table(table)
    cols = sorted(c for c in df.columns if c not in exclude)
    row_hash = F.xxhash64(*[F.col(f"`{c}`") for c in cols]).cast("decimal(38,0)")
    r = df.agg(
        F.count(F.lit(1)).alias("rows"),
        F.countDistinct(*[F.col(f"`{k}`") for k in keys]).alias("distinct_keys"),
        F.coalesce(F.sum(row_hash), F.lit(0).cast("decimal(38,0)")).alias("checksum"),
    ).first()
    return {
        "rows": int(r["rows"]),
        "distinct_keys": int(r["distinct_keys"]),
        "checksum": str(r["checksum"]),
        "columns": cols,
    }
