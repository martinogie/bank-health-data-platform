# Databricks notebook source
# MAGIC %md
# MAGIC # 03_gold — one row per bank per quarter, plus exports for Snowflake
# MAGIC
# MAGIC **`gold.bank_quarter`** (grain: `cert` × `report_date`) = silver financials + bank attributes +
# MAGIC derived ratios + complaint counts:
# MAGIC - `net_income_qtr_k`: FDIC income is **year-to-date**, so Q2 = YTD(Q2) − YTD(Q1), Q1 = YTD(Q1).
# MAGIC - QoQ / YoY growth of loans, deposits, assets (only when the prior quarter exists exactly).
# MAGIC - CRE concentration = construction + multifamily + non-farm non-residential loans,
# MAGIC   as % of gross loans and % of equity (NULL if all three parts are missing).
# MAGIC - `cblr_reporter`, `is_foreign_office` (IBA flag or FDIC class OI): capital ratios these
# MAGIC   banks don't report are NULL here (FDIC files them as 0). Banks with both ratios 0 in 4+
# MAGIC   quarters but no foreign flag go to `gold.capital_zero_review` (not excluded).
# MAGIC   `bank_age_years` from the establishment date.
# MAGIC - Complaint counts per bank-quarter, **only for quarters fully covered by complaint data**
# MAGIC   (otherwise NULL, so a half-loaded quarter never looks artificially quiet).
# MAGIC   Banks with no CFPB match get 0 and `has_cfpb_match = false`.
# MAGIC
# MAGIC **Exports**: silver tables written as Parquet to `gold.exports/<table>/` for Airflow to load
# MAGIC into Snowflake `RAW`.

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

NOTEBOOK = "03_gold"

fin = spark.table(f"{SILVER}.fdic_financials")
inst = spark.table(f"{SILVER}.fdic_institutions")
fail = spark.table(f"{SILVER}.fdic_failures")
comp = spark.table(f"{SILVER}.cfpb_complaints")
company_map = spark.table(f"{SILVER}.company_bank_map")

# COMMAND ----------

# MAGIC %md ## Prior-quarter and prior-year values

# COMMAND ----------

LAG_COLS = ["net_income_ytd_k", "gross_loans_k", "total_deposits_k", "total_assets_k"]


def shifted(months: int, suffix: str):
    """Each bank's values moved forward `months` months, to join as 'prior period' values.

    Joining on the exact quarter-end means a missing quarter gives NULL, never a wrong lag.
    """
    return fin.select(
        "cert",
        F.last_day(F.add_months("report_date", months)).alias("report_date"),
        *[F.col(c).alias(f"{c}_{suffix}") for c in LAG_COLS],
    )


def pct_change(cur, prev):
    return F.when(F.col(prev) > 0, F.round((F.col(cur) / F.col(prev) - 1) * 100, 4))


def safe_pct(num, den):
    return F.when(den > 0, F.round(num / den * 100, 4))


CRE_PARTS = ["construction_loans_k", "multifamily_loans_k", "nonfarm_nonres_loans_k"]
# A missing part counts as 0 only if at least one part is reported; if all three are missing,
# CRE is missing (NULL), not 0%.
cre_k = F.when(F.coalesce(*[F.col(c) for c in CRE_PARTS]).isNotNull(),
               sum(F.coalesce(F.col(c), F.lit(0.0)) for c in CRE_PARTS))


bq = (
    fin.drop("_source_file", "_ingested_at")
    .join(shifted(3, "prev_q"), ["cert", "report_date"], "left")
    .join(shifted(12, "prev_y"), ["cert", "report_date"], "left")
    .withColumn("year", F.year("report_date"))
    .withColumn("quarter", F.quarter("report_date"))
    .withColumn("net_income_qtr_k",
                F.when(F.col("quarter") == 1, F.col("net_income_ytd_k"))
                 .otherwise(F.col("net_income_ytd_k") - F.col("net_income_ytd_k_prev_q")))
    .withColumn("loan_growth_qoq_pct", pct_change("gross_loans_k", "gross_loans_k_prev_q"))
    .withColumn("loan_growth_yoy_pct", pct_change("gross_loans_k", "gross_loans_k_prev_y"))
    .withColumn("deposit_growth_qoq_pct", pct_change("total_deposits_k", "total_deposits_k_prev_q"))
    .withColumn("deposit_growth_yoy_pct", pct_change("total_deposits_k", "total_deposits_k_prev_y"))
    .withColumn("asset_growth_yoy_pct", pct_change("total_assets_k", "total_assets_k_prev_y"))
    .withColumn("cre_loans_k", cre_k)
    .withColumn("cre_to_loans_pct", safe_pct(cre_k, F.col("gross_loans_k")))
    .withColumn("cre_to_equity_pct", safe_pct(cre_k, F.col("total_equity_k")))
    .withColumn("equity_to_assets_pct", safe_pct(F.col("total_equity_k"), F.col("total_assets_k")))
    .withColumn("loans_to_deposits_pct", safe_pct(F.col("gross_loans_k"), F.col("total_deposits_k")))
    .withColumn("log_total_assets",
                F.when(F.col("total_assets_k") > 0, F.round(F.log(F.col("total_assets_k") * 1000), 6)))
    .withColumn("asset_size_tier",
                F.when(F.col("total_assets_k") < 1e5, "1: <$100M")
                 .when(F.col("total_assets_k") < 1e6, "2: $100M-$1B")
                 .when(F.col("total_assets_k") < 1e7, "3: $1B-$10B")
                 .when(F.col("total_assets_k") < 1e8, "4: $10B-$100B")
                 .otherwise("5: >$100B"))
    .drop(*[f"{c}_prev_q" for c in LAG_COLS], *[f"{c}_prev_y" for c in LAG_COLS])
)

# COMMAND ----------

# MAGIC %md ## Bank attributes, failures, complaints

# COMMAND ----------

first_failure = fail.filter("cert is not null").groupBy("cert").agg(F.min("fail_date").alias("fail_date"))

# Quarters fully covered by complaint data: quarter start >= first complaint date, end <= last.
cov = comp.agg(F.min("date_received").alias("lo"), F.max("date_received").alias("hi")).first()
quarter_start = F.trunc("report_date", "quarter")

complaints_q = (
    comp.filter("cert is not null")
    .withColumn("report_date", F.last_day(F.add_months(F.trunc("date_received", "quarter"), 2)))
    .groupBy("cert", "report_date")
    .agg(
        F.count("*").alias("complaints"),
        F.sum(F.when(~F.col("timely_response"), 1).otherwise(0)).alias("complaints_untimely"),
        F.sum(F.when(F.col("company_response") == "Closed with monetary relief", 1).otherwise(0))
         .alias("complaints_monetary_relief"),
    )
)
matched_certs = company_map.filter("cert is not null").select("cert").distinct().withColumn("has_cfpb_match", F.lit(True))

covered = (quarter_start >= F.lit(cov["lo"])) & (F.col("report_date") <= F.lit(cov["hi"])) if cov["lo"] else F.lit(False)

# Inner join: gold covers institutions in FDIC's institutions dataset. The few call-report
# filers missing from it (flagged in 02_silver) are left out and counted below.
gold = (
    bq.join(inst.select("cert", "bank_name", "city", "state_code", "bank_class", "primary_regulator",
                        "holding_company", "specialization", "is_active", "established_date"), "cert", "inner")
    .join(first_failure, "cert", "left")
    .join(matched_certs, "cert", "left")
    .join(complaints_q, ["cert", "report_date"], "left")
    .withColumn("bank_age_years",
                F.round(F.months_between("report_date", "established_date") / 12, 2))
    .withColumn("has_cfpb_match", F.coalesce("has_cfpb_match", F.lit(False)))
    .withColumn("complaint_data_covered", covered)
    .withColumn("complaints", F.when(covered, F.coalesce("complaints", F.lit(0))))
    .withColumn("complaints_untimely", F.when(covered, F.coalesce("complaints_untimely", F.lit(0))))
    .withColumn("complaints_monetary_relief", F.when(covered, F.coalesce("complaints_monetary_relief", F.lit(0))))
    .withColumn("complaints_per_1b_deposits",
                F.when(covered & (F.col("total_deposits_k") > 0),
                       F.round(F.col("complaints") / (F.col("total_deposits_k") / 1e6), 4)))
)

# COMMAND ----------

# MAGIC %md ## Reporting framework: CBLR banks and US offices of foreign banks

# COMMAND ----------

# FDIC files a capital ratio as 0 when it is not reported:
# - CBLR banks (cblr_indicator = 1, from 2020) file no risk-based capital ratio;
# - US offices of foreign banks report no capital ratios. Identified by the IBA flag or the
#   FDIC class OI (insured US branch of a foreign-chartered bank).
# Those ratios become NULL here (silver keeps FDIC's 0), so "0.00%" is never shown or used.
# Everyone else keeps their reported values, including real zero or negative capital at
# insolvent banks.
# Offices of foreign banks that neither the IBA flag nor class OI identify. Each entry was checked
# by a person after appearing in gold.capital_zero_review; keep the evidence next to the entry.
FOREIGN_OFFICE_OVERRIDES = {
    # The Bank of East Asia Ltd., New York (Hong Kong bank). FDIC class NC, IBA flag 0, yet Tier 1
    # leverage and total risk-based capital are both 0 in all 53 quarters it reports (2013 Q2 to
    # 2026 Q2), the pattern of a foreign-bank office. FDIC also lists an OI branch with the same
    # name (CERT 33646). Reviewed 2026-10-25.
    26028: "The Bank of East Asia Ltd., NY",
}
override_certs = list(FOREIGN_OFFICE_OVERRIDES)

both_zero = ((F.col("tier1_leverage_pct") == 0) & (F.col("total_risk_based_capital_pct") == 0)
             & F.col("cblr_indicator").isNull())
gold = (
    gold.withColumn("cblr_reporter", F.coalesce(F.col("cblr_indicator") == 1, F.lit(False)))
    .withColumn("is_foreign_office",
                F.coalesce((F.col("iba_flag") == 1) | (F.col("bank_class") == "OI")
                           | F.col("cert").isin(override_certs), F.lit(False)))
    .withColumn("foreign_office_rule",
                F.when(F.col("iba_flag") == 1, "IBA flag").when(F.col("bank_class") == "OI", "FDIC class OI")
                 .when(F.col("cert").isin(override_certs), "reviewed override"))
    # Possible foreign office not flagged by IBA or OI: both ratios 0 and no CBLR flag in 4+ quarters.
    # Not excluded automatically; listed in gold.capital_zero_review for a person to check.
    .withColumn("zero_capital_quarters",
                F.sum(F.when(both_zero & ~F.col("is_foreign_office"), 1).otherwise(0)).over(Window.partitionBy("cert")))
    .withColumn("capital_zero_review", both_zero & ~F.col("is_foreign_office") & (F.col("zero_capital_quarters") >= 4))
    .withColumn("total_risk_based_capital_pct",
                F.when(~F.col("cblr_reporter") & ~F.col("is_foreign_office"), F.col("total_risk_based_capital_pct")))
    .withColumn("tier1_leverage_pct", F.when(~F.col("is_foreign_office"), F.col("tier1_leverage_pct")))
    .drop("zero_capital_quarters")
)

gold.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{GOLD}.bank_quarter")
gold = spark.table(f"{GOLD}.bank_quarter")
n_gold = gold.count()
print(f"{GOLD}.bank_quarter: {n_gold} rows; complaint data covers {cov['lo']} .. {cov['hi']}")

# COMMAND ----------

T = "gold.bank_quarter"
dq_unique(gold, T, ["cert", "report_date"])
known = fin.join(inst.select("cert"), "cert", "inner").count()
dq_check(T, "one gold row per silver financials row of a known institution", abs(n_gold - known))
dq_check(T, "financials rows left out (not in institutions dataset)", fin.count() - known, level="warn")
dq_not_null(gold, T, ["bank_name", "asset_size_tier"])
dq_range(gold, T, "complaints", low=0, level="error")
counted = gold.agg(F.sum("complaints")).first()[0] or 0
expected = (complaints_q.join(gold.filter("complaint_data_covered").select("cert", "report_date"),
                              ["cert", "report_date"]).agg(F.sum("complaints")).first()[0] or 0)
dq_check(T, "every complaint for a covered quarter counted", abs(counted - expected))
dq_range(gold, T, "cre_to_loans_pct", low=0, high=100)
dq_check(T, "no 0% total risk-based capital for CBLR banks or foreign offices",
         gold.filter("(cblr_reporter or is_foreign_office) and total_risk_based_capital_pct is not null").count())
kept = gold.select("cert", "report_date", "is_foreign_office", F.col("tier1_leverage_pct").alias("g_t1")).join(
    fin.select("cert", "report_date", F.col("tier1_leverage_pct").alias("s_t1")), ["cert", "report_date"])
dq_check(T, "Tier 1 leverage as reported for every bank that is not a foreign office (incl. real 0 / negative)",
         kept.filter("not is_foreign_office and not (g_t1 <=> s_t1)").count())
dq_check(T, "no Tier 1 leverage for foreign offices",
         gold.filter("is_foreign_office and tier1_leverage_pct is not null").count())
dq_check(T, "CRE NULL only when all three CRE parts are NULL",
         gold.filter("cre_loans_k is null and coalesce(construction_loans_k, multifamily_loans_k, "
                     "nonfarm_nonres_loans_k) is not null").count())
foreign_by_rule = {r["foreign_office_rule"]: {"banks": r["banks"], "bank_quarters": r["bank_quarters"]}
                   for r in gold.filter("is_foreign_office").groupBy("foreign_office_rule")
                   .agg(F.countDistinct("cert").alias("banks"), F.count("*").alias("bank_quarters")).collect()}
print("Foreign offices by rule:", foreign_by_rule)

# Review list: possible foreign offices the flags missed (scored as normal banks until checked)
review = (gold.filter("capital_zero_review")
          .groupBy("cert", "bank_name", "state_code", "bank_class")
          .agg(F.count("*").alias("zero_capital_quarters"), F.min("report_date").alias("first_quarter"),
               F.max("report_date").alias("last_quarter"))
          .withColumn("reviewed_at", F.lit(RUN_TS)))
review.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{GOLD}.capital_zero_review")
review_rows = [r.asDict() for r in review.orderBy("cert").collect()]
dq_check(T, "banks with 4+ zero-capital quarters not flagged foreign (see gold.capital_zero_review)",
         len(review_rows), level="warn")
for r in review_rows:
    print(f"REVIEW: CERT {r['cert']} {r['bank_name']} ({r['state_code']}, class {r['bank_class']}): "
          f"both capital ratios 0 in {r['zero_capital_quarters']} quarters, "
          f"{r['first_quarter']} to {r['last_quarter']}. Check whether it is a foreign-bank office.")

display(gold.filter("complaint_data_covered").orderBy(F.col("complaints").desc())
        .select("report_date", "cert", "bank_name", "total_deposits_k", "complaints",
                "complaints_per_1b_deposits", "complaints_untimely").limit(15))

# COMMAND ----------

# MAGIC %md ## Exports for Snowflake (Parquet)

# COMMAND ----------

EXPORT_TABLES = {
    "fdic_financials": f"{SILVER}.fdic_financials",
    "fdic_institutions": f"{SILVER}.fdic_institutions",
    "fdic_failures": f"{SILVER}.fdic_failures",
    "cfpb_complaints": f"{SILVER}.cfpb_complaints",
}
exported = {}
for folder, table in EXPORT_TABLES.items():
    df = spark.table(table).drop("_source_file")
    small = folder in ("fdic_institutions", "fdic_failures")
    (df.coalesce(1) if small else df).write.mode("overwrite").parquet(f"{EXPORTS}/{folder}")
    exported[folder] = spark.read.parquet(f"{EXPORTS}/{folder}").count()
    dq_check(f"exports/{folder}", "export row count matches table", abs(exported[folder] - df.count()))
    print(f"exported {table} -> {EXPORTS}/{folder} ({exported[folder]} rows)")

# COMMAND ----------

dq_summary = dq_finish()
jpm = (gold.filter("cert = 628").orderBy(F.col("report_date").desc())
       .select("report_date", "total_assets_k", "net_income_qtr_k", "tier1_leverage_pct", "complaints").first())
summary = {
    "bank_quarter_rows": n_gold,
    "quarters": gold.agg(F.countDistinct("report_date").alias("n"), F.min("report_date").cast("string").alias("first"),
                         F.max("report_date").cast("string").alias("last")).first().asDict(),
    "foreign_offices": foreign_by_rule,
    "capital_zero_review": review_rows,
    "complaint_coverage": [str(cov["lo"]), str(cov["hi"])],
    "exports": exported,
    "spot_check_jpmorgan_628": {k: (str(v) if k == "report_date" else v) for k, v in jpm.asDict().items()} if jpm else None,
    "data_quality": dq_summary,
}
print(json.dumps(summary, indent=2, default=str))
dbutils.notebook.exit(json.dumps(summary, default=str))
