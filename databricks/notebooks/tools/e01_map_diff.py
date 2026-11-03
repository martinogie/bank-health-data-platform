# Databricks notebook source
# MAGIC %md
# MAGIC # e01_map_diff — before/after of two company → bank maps (E-01)
# MAGIC
# MAGIC Read-only. Compares `before` and `after` (two `company_bank_map` tables, e.g. prod
# MAGIC `bank_health.silver` and a dev rebuild in `bank_health_dev.silver_fullcheck`): companies and
# MAGIC complaints matched, the match rate, every company whose bank changed, and the companies sent
# MAGIC to review.

# COMMAND ----------

# MAGIC %run ../_common

# COMMAND ----------

dbutils.widgets.text("before", "bank_health.silver.company_bank_map", "Map before")
dbutils.widgets.text("after", "bank_health_dev.silver_fullcheck.company_bank_map", "Map after")
before = spark.table(dbutils.widgets.get("before").strip())
after = spark.table(dbutils.widgets.get("after").strip())


def totals(m):
    r = m.agg(
        F.count("*").alias("companies"),
        F.sum(F.when(F.col("cert").isNotNull(), 1).otherwise(0)).alias("companies_matched"),
        F.sum(F.when(F.col("cert").isNotNull(), F.col("complaints")).otherwise(0)).alias(
            "complaints_matched"
        ),
        F.sum("complaints").alias("complaints"),
    ).first()
    return {
        "companies": int(r["companies"]),
        "companies_matched": int(r["companies_matched"]),
        "complaints_matched": int(r["complaints_matched"]),
        "complaints": int(r["complaints"]),
        "match_rate_pct": round(100 * r["complaints_matched"] / r["complaints"], 2),
    }


changed = (
    before.select(
        "company",
        "complaints",
        F.col("cert").alias("cert_before"),
        F.col("matched_bank_name").alias("bank_before"),
    )
    .join(
        after.select(
            "company",
            F.col("cert").alias("cert_after"),
            F.col("matched_bank_name").alias("bank_after"),
            "match_method",
        ),
        "company",
        "full",
    )
    .filter(~F.col("cert_before").eqNullSafe(F.col("cert_after")))
    .orderBy(F.col("complaints").desc_nulls_last())
)
review_cols = [c for c in ("company", "complaints", "review_cert", "review_reason") if c in after.columns]
for_review = (
    after.filter("review_reason is not null").select(*review_cols)
    if "review_reason" in after.columns
    else None
)
result = {
    "before": totals(before),
    "after": totals(after),
    "changed": [r.asDict() for r in changed.collect()],
    "for_review": [r.asDict() for r in for_review.orderBy(F.col("complaints").desc()).collect()]
    if for_review is not None
    else [],
}
print(json.dumps({k: result[k] for k in ("before", "after")}, indent=1))
dbutils.notebook.exit(json.dumps(result, default=str))
