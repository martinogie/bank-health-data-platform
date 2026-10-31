# Databricks notebook source
# MAGIC %md
# MAGIC # 02_silver — clean, typed, de-duplicated, matched
# MAGIC
# MAGIC | Silver table | Grain | What happens |
# MAGIC |---|---|---|
# MAGIC | `fdic_financials` | bank × quarter | types, readable names (amounts stay in **$ thousands**, suffix `_k`), newest load wins |
# MAGIC | `fdic_institutions` | bank | latest snapshot per bank, dates parsed |
# MAGIC | `fdic_failures` | failure | latest snapshot |
# MAGIC | `company_bank_map` | CFPB company | CFPB company name → FDIC `cert` (entity resolution) |
# MAGIC | `cfpb_complaints` | complaint | types, blanks → NULL, newest load wins, matched `cert` added |
# MAGIC
# MAGIC **How each table is loaded (D-02).**
# MAGIC - `fdic_financials` and `cfpb_complaints`: **incremental**. Only bronze rows ingested since the
# MAGIC   last load are read; a Delta MERGE on the key (`cert, report_date` / `complaint_id`) inserts
# MAGIC   new keys and updates amended ones (`_silver_merge`). Re-running with nothing new changes nothing.
# MAGIC - `fdic_institutions`, `fdic_failures`, `company_bank_map`: rebuilt in full each run (small
# MAGIC   snapshots). When the map changes, only the affected complaints get a new `cert`.
# MAGIC - `silver_mode=full` rebuilds everything from all of bronze (also done automatically when a
# MAGIC   table is missing or its columns changed). Both modes give identical tables.
# MAGIC
# MAGIC Data-quality results go to `silver.dq_results`.

# COMMAND ----------

# MAGIC %run ./_common

# COMMAND ----------

# MAGIC %run ./_silver_merge

# COMMAND ----------

NOTEBOOK = "02_silver"

dbutils.widgets.text("silver_mode", "incremental", "incremental, or full (rebuild from all of bronze)")
# Dev checks only: write a full rebuild into another schema to compare it with the incremental tables.
dbutils.widgets.text("silver_schema", "silver", "Target schema (dev comparisons only)")
SILVER_MODE = dbutils.widgets.get("silver_mode").strip().lower() or "incremental"
if SILVER_MODE not in SILVER_MODES:
    raise ValueError(f"silver_mode must be one of {SILVER_MODES}, not {SILVER_MODE!r}")
SILVER_SCHEMA = dbutils.widgets.get("silver_schema").strip() or "silver"
if SILVER_SCHEMA != "silver" and IS_PROD:
    raise ValueError("silver_schema can only be changed in dev")
# Dev checks only: read bronze from another catalog (e.g. prod's bank_health, READ-ONLY) to rebuild
# prod's silver into a dev scratch schema and compare it with prod's silver. Writes stay in dev.
dbutils.widgets.text("bronze_catalog", "", "Read bronze from this catalog (dev comparisons only)")
BRONZE_CATALOG = dbutils.widgets.get("bronze_catalog").strip()
if BRONZE_CATALOG:
    if IS_PROD:
        raise ValueError("bronze_catalog can only be changed in dev")
    if SILVER_SCHEMA == "silver":
        raise ValueError("reading another catalog's bronze must write a scratch schema, never dev silver")
    BRONZE = f"{BRONZE_CATALOG}.bronze"  # only this notebook's reads; _common's other names stay dev
OUT = f"{CATALOG}.{SILVER_SCHEMA}"
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {OUT}")
LOADS = {}  # per table: how it was loaded, rows inserted/updated
print(f"silver_mode={SILVER_MODE}, reading {BRONZE}, writing {OUT}")


def save(df, table):
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{OUT}.{table}")
    n = spark.table(f"{OUT}.{table}").count()
    print(f"{OUT}.{table}: {n} rows")
    return n


def load_incrementally(table, bronze_df, bronze_keys, project, silver_keys, keep_on_update=()):
    """Full rebuild or MERGE, as the module docs describe. `project` turns bronze rows into silver
    columns. Returns the silver row count."""
    target = f"{OUT}.{table}"
    reason = (
        "silver_mode=full"
        if SILVER_MODE == "full"
        else schema_difference(target, project(bronze_df.limit(0)))
    )
    order = ["_ingested_at", "_file_modified_at"]
    if reason:
        print(f"{target}: full rebuild ({reason})")
        n = save(project(latest_per_key(bronze_df, bronze_keys, order)), table)
        LOADS[table] = {"mode": "full", "reason": reason, "rows": n}
        return n
    batch = project(latest_per_key(newer_than(bronze_df, target), bronze_keys, order))
    stats = merge_newest(target, batch, silver_keys, keep_on_update)
    n = spark.table(target).count()
    print(f"{target}: incremental, {stats['inserted']} inserted, {stats['updated']} updated, {n} rows")
    LOADS[table] = {"mode": "incremental", **stats, "rows": n}
    return n


# COMMAND ----------

# MAGIC %md ## FDIC financials

# COMMAND ----------

# FDIC field -> silver column. Amounts are $ thousands; income items are year-to-date.
FINANCIAL_COLUMNS = {
    "asset": "total_assets_k",
    "dep": "total_deposits_k",
    "depdom": "domestic_deposits_k",
    "bro": "brokered_deposits_k",
    "lnlsnet": "net_loans_k",
    "lnlsgr": "gross_loans_k",
    "eq": "total_equity_k",
    "ore": "other_real_estate_owned_k",
    "netinc": "net_income_ytd_k",
    "elnatr": "provision_for_losses_ytd_k",
    "roa": "roa_pct",
    "roe": "roe_pct",
    "nimy": "net_interest_margin_pct",
    "eeffr": "efficiency_ratio_pct",
    "nclnlsr": "noncurrent_loans_pct",
    "ntlnlsr": "net_chargeoff_rate_pct",
    "p3asset": "past_due_30_89_k",
    "p9asset": "past_due_90_plus_k",
    "naasset": "nonaccrual_assets_k",
    "rbc1aaj": "tier1_leverage_pct",
    "rbcrwaj": "total_risk_based_capital_pct",
    "lnre": "real_estate_loans_k",
    "lnrecons": "construction_loans_k",
    "lnremult": "multifamily_loans_k",
    "lnrenres": "nonfarm_nonres_loans_k",
    "lnreres": "residential_re_loans_k",
    "lnci": "commercial_industrial_loans_k",
    "lncon": "consumer_loans_k",
    "lnag": "agricultural_loans_k",
}

fin_bronze = spark.table(f"{BRONZE}.fdic_financials")


def project_financials(df):
    return df.select(
        F.col("cert").cast("int").alias("cert"),
        F.to_date("repdte", "yyyyMMdd").alias("report_date"),
        *[F.col(src).cast("double").alias(dst) for src, dst in FINANCIAL_COLUMNS.items()],
        # Reporting flags (0/1). CBLRIND is NULL before 2020, when the CBLR framework did not exist.
        F.col("cblrind").cast("int").alias("cblr_indicator"),
        F.col("iba").cast("int").alias("iba_flag"),
        "_source_file",
        "_ingested_at",
    )


n_fin = load_incrementally(
    "fdic_financials", fin_bronze, ["cert", "repdte"], project_financials, ["cert", "report_date"]
)
fin = spark.table(f"{OUT}.fdic_financials")

T = "silver.fdic_financials"
dq_check(T, "row_count > 0", int(n_fin == 0))
dq_not_null(fin, T, ["cert", "report_date"])
dq_unique(fin, T, ["cert", "report_date"])
dq_check(
    T,
    "report_date is a quarter-end",
    fin.filter(
        ~((F.month("report_date") % 3 == 0) & (F.col("report_date") == F.last_day("report_date")))
    ).count(),
)
dq_check(
    T,
    "bronze rows de-duplicated, none lost",
    abs(fin_bronze.select("cert", "repdte").distinct().count() - n_fin),
)
dq_range(fin, T, "total_assets_k", low=0)
dq_range(fin, T, "noncurrent_loans_pct", low=0, high=100)
dq_range(fin, T, "tier1_leverage_pct", low=-50, high=100)
dq_range(fin, T, "efficiency_ratio_pct", low=0, high=1000)
dq_check(T, "cblr_indicator is 0, 1 or NULL", fin.filter("cblr_indicator not in (0, 1)").count())
dq_check(T, "iba_flag is 0 or 1", fin.filter("iba_flag is null or iba_flag not in (0, 1)").count())
display(
    fin.groupBy("report_date")
    .agg(
        F.count("*").alias("banks"), F.round(F.sum("total_assets_k") / 1e9, 2).alias("total_assets_trillions")
    )
    .orderBy("report_date")
)

# COMMAND ----------

# MAGIC %md ## FDIC institutions (latest snapshot per bank)

# COMMAND ----------


def mdy(col_name):
    """FDIC 'MM/dd/yyyy' text -> date; '12/31/9999' means 'no end date' -> NULL."""
    return F.when(F.col(col_name) == "12/31/9999", None).otherwise(F.to_date(col_name, "MM/dd/yyyy"))


inst = latest_per_key(
    spark.table(f"{BRONZE}.fdic_institutions"), ["cert"], ["_source_file", "_ingested_at"]
).select(
    F.col("cert").cast("int").alias("cert"),
    clean_str("name").alias("bank_name"),
    clean_str("city").alias("city"),
    clean_str("stalp").alias("state_code"),
    clean_str("stname").alias("state_name"),
    clean_str("zip").alias("zip_code"),
    (F.col("active") == "1").alias("is_active"),
    clean_str("bkclass").alias("bank_class"),
    clean_str("charter").alias("charter_agency_code"),
    clean_str("regagnt").alias("primary_regulator"),
    clean_str("specgrpn").alias("specialization"),
    clean_str("namehcr").alias("holding_company"),
    clean_str("rssdhcr").alias("holding_company_rssd"),
    clean_str("fed_rssd").alias("fed_rssd"),
    mdy("estymd").alias("established_date"),
    mdy("insdate").alias("insured_date"),
    mdy("endefymd").alias("end_date"),
    F.col("changec1").cast("int").alias("last_change_code"),
    F.col("asset").cast("double").alias("total_assets_k"),
    F.col("dep").cast("double").alias("total_deposits_k"),
    F.col("offices").cast("int").alias("offices"),
    clean_str("webaddr").alias("website"),
    mdy("repdte").alias("as_of_date"),
    "_source_file",
    "_ingested_at",
)
LOADS["fdic_institutions"] = {"mode": "full", "reason": "snapshot", "rows": save(inst, "fdic_institutions")}
inst = spark.table(f"{OUT}.fdic_institutions")

T = "silver.fdic_institutions"
dq_not_null(inst, T, ["cert", "bank_name"])
dq_unique(inst, T, ["cert"])
# A few call-report filers are not in the institutions dataset at all (FDIC's API returns
# nothing for them; most report zero deposits, e.g. uninsured trust companies). They stay in
# silver as reported and are left out of gold, so this is a warning, not an error.
dq_check(
    T,
    "financials certs missing from institutions (excluded from gold)",
    fin.select("cert").distinct().join(inst, "cert", "left_anti").count(),
    level="warn",
)

# COMMAND ----------

# MAGIC %md ## FDIC failures (latest snapshot)

# COMMAND ----------

fail_bronze = spark.table(f"{BRONZE}.fdic_failures")
latest_file = fail_bronze.agg(F.max("_source_file")).first()[0]
# The same file can be ingested more than once (re-uploaded under the same name), so also
# keep only the newest load of each failure record.
fail = latest_per_key(
    fail_bronze.filter(F.col("_source_file") == latest_file), ["id"], ["_ingested_at", "_file_modified_at"]
).select(
    F.col("id").cast("int").alias("failure_id"),
    F.col("cert").cast("int").alias("cert"),
    clean_str("name").alias("bank_name"),
    clean_str("city").alias("city"),
    clean_str("pstalp").alias("state_code"),
    F.to_date("faildate", "M/d/yyyy").alias("fail_date"),
    clean_str("restype1").alias("resolution_type"),
    clean_str("chclass1").alias("charter_class"),
    F.col("qbfasset").cast("double").alias("total_assets_k"),
    F.col("qbfdep").cast("double").alias("total_deposits_k"),
    F.col("cost").cast("double").alias("estimated_loss_k"),
    clean_str("bidname").alias("acquirer"),
    "_source_file",
    "_ingested_at",
)
LOADS["fdic_failures"] = {"mode": "full", "reason": "snapshot", "rows": save(fail, "fdic_failures")}
fail = spark.table(f"{OUT}.fdic_failures")

T = "silver.fdic_failures"
dq_not_null(fail, T, ["failure_id", "fail_date"])
dq_unique(fail, T, ["failure_id"])
dq_check(
    T,
    "cert appears in more than one failure",
    fail.filter("cert is not null").groupBy("cert").count().filter("count > 1").count(),
    level="warn",
)
display(fail.orderBy(F.col("fail_date").desc()).limit(5))

# COMMAND ----------

# MAGIC %md ## CFPB complaints

# COMMAND ----------


def project_complaints(df):
    # Column order as before (company first). cert and match_method start empty and are set from
    # company_bank_map below, for new complaints and for any whose company's match changed.
    return df.select(
        clean_str("company").alias("company"),
        F.col("complaint_id").cast("bigint").alias("complaint_id"),
        F.to_date(F.to_timestamp("date_received")).alias("date_received"),
        F.to_date(F.to_timestamp("date_sent_to_company")).alias("date_sent_to_company"),
        clean_str("product").alias("product"),
        clean_str("sub_product").alias("sub_product"),
        clean_str("issue").alias("issue"),
        clean_str("sub_issue").alias("sub_issue"),
        clean_str("company_response_to_consumer").alias("company_response"),
        clean_str("company_public_response").alias("company_public_response"),
        F.when(F.col("timely_response") == "Yes", True)
        .when(F.col("timely_response") == "No", False)
        .alias("timely_response"),
        clean_str("submitted_via").alias("submitted_via"),
        clean_str("state").alias("state_code"),
        clean_str("zip_code").alias("zip_code"),
        clean_str("tags").alias("tags"),
        "_source_file",
        "_ingested_at",
        F.lit(None).cast("int").alias("cert"),
        F.lit(None).cast("string").alias("match_method"),
    )


comp_bronze = spark.table(f"{BRONZE}.cfpb_complaints")
load_incrementally(
    "cfpb_complaints",
    comp_bronze,
    ["complaint_id"],
    project_complaints,
    ["complaint_id"],
    keep_on_update=("cert", "match_method"),  # an amended complaint keeps its match until rematch
)
comp = spark.table(f"{OUT}.cfpb_complaints")  # every complaint; the map below is built from all of them

# COMMAND ----------

# MAGIC %md
# MAGIC ## Entity resolution: CFPB company → FDIC bank (`cert`)
# MAGIC
# MAGIC CFPB names the company (often the **holding company**, e.g. "JPMORGAN CHASE & CO."); FDIC
# MAGIC data is per **bank** with the holding company in `holding_company`. Rules, in order:
# MAGIC 1. **manual_override** — a short hand-checked list (CERTs verified in FDIC institutions).
# MAGIC 2. **bank_name** — normalised CFPB name equals a normalised FDIC bank name.
# MAGIC 3. **holding_company** — normalised CFPB name equals a normalised FDIC holding-company name;
# MAGIC    complaints go to the group's **largest bank**. Skipped for LLC/LP names (bank holding
# MAGIC    companies are not LLCs; avoids e.g. "Flagship Financial Group LLC" matching a bank HC).
# MAGIC
# MAGIC Normalising: upper-case, `&`→AND, punctuation removed, FDIC abbreviations expanded
# MAGIC (BCORP→BANCORP, FINL→FINANCIAL), legal suffixes dropped (INC, CORP, CO, N.A., ...), spaces removed.
# MAGIC Only banks active at some point during the complaint period are candidates.
# MAGIC Unmatched companies are mostly not FDIC-insured banks (debt collectors, fintechs,
# MAGIC data brokers, servicers, credit unions).

# COMMAND ----------

MANUAL_OVERRIDES = [
    # (CFPB company, FDIC cert, why)
    (
        "TD BANK US HOLDING COMPANY",
        18409,
        "FDIC HC name is TORONTO-DOMINION BANK THE; lead bank TD Bank, N.A.",
    ),
    ("FIFTH THIRD FINANCIAL CORPORATION", 6672, "FDIC HC name is FIFTH THIRD BCORP"),
    ("NEW YORK COMMUNITY BANCORP INC", 32541, "Renamed Flagstar Financial; FDIC record has no HC name"),
    ("SANTANDER HOLDINGS USA, INC.", 29950, "FDIC HC name is BANCO SANTANDER SA"),
]
ABBREVIATIONS = [
    ("FINL", "FINANCIAL"),
    ("SVCS", "SERVICES"),
    ("BANCORPORATION", "BANCORP"),
    ("BCORP", "BANCORP"),
]
LEGAL_SUFFIXES = (
    r"\b(INCORPORATED|INC|CORPORATION|CORP|COMPANY|CO|LLC|L L C|LP|L P|LTD|THE|"
    r"NATIONAL ASSOCIATION|N A|NA|FSB|F S B|SSB|HOLDINGS|HOLDING)\b"
)


def normalise(col_name):
    c = F.upper(F.col(col_name))
    c = F.regexp_replace(c, "&", " AND ")
    c = F.regexp_replace(c, "[^A-Z0-9 ]", " ")
    for short, full in ABBREVIATIONS:
        c = F.regexp_replace(c, rf"\b{short}\b", full)
    c = F.regexp_replace(c, LEGAL_SUFFIXES, " ")
    c = F.regexp_replace(c, r"\s+", "")
    return F.when(c == "", None).otherwise(c)


complaint_start = comp.agg(F.min("date_received")).first()[0]
candidates = inst.filter(F.col("is_active") | (F.col("end_date") >= F.lit(complaint_start)))

by_bank_name = latest_per_key(
    candidates.withColumn("company_key", normalise("bank_name")), ["company_key"], ["total_assets_k"]
).select("company_key", F.col("cert").alias("cert_by_name"))
by_holding_co = latest_per_key(
    candidates.filter("holding_company is not null").withColumn("company_key", normalise("holding_company")),
    ["company_key"],
    ["total_assets_k"],
).select("company_key", F.col("cert").alias("cert_by_hc"))
overrides = spark.createDataFrame(MANUAL_OVERRIDES, "company string, cert_override int, override_note string")

companies = (
    comp.groupBy("company")
    .agg(F.count("*").alias("complaints"))
    .withColumn("company_key", normalise("company"))
    .withColumn("is_llc_or_lp", F.upper("company").rlike(r"\b(LLC|L\.L\.C|LP|L\.P)\b"))
)
company_map = (
    companies.join(overrides, "company", "left")
    .join(by_bank_name, "company_key", "left")
    .join(by_holding_co, "company_key", "left")
    .withColumn("cert_by_hc", F.when(~F.col("is_llc_or_lp"), F.col("cert_by_hc")))
    .withColumn("cert", F.coalesce("cert_override", "cert_by_name", "cert_by_hc"))
    .withColumn(
        "match_method",
        F.when(F.col("cert_override").isNotNull(), "manual_override")
        .when(F.col("cert_by_name").isNotNull(), "bank_name")
        .when(F.col("cert_by_hc").isNotNull(), "holding_company")
        .otherwise("unmatched"),
    )
    .join(inst.select("cert", F.col("bank_name").alias("matched_bank_name")), "cert", "left")
    .select(
        "company", "company_key", "cert", "matched_bank_name", "match_method", "override_note", "complaints"
    )
)
LOADS["company_bank_map"] = {
    "mode": "full",
    "reason": "small; built from all complaints",
    "rows": save(company_map, "company_bank_map"),
}
company_map = spark.table(f"{OUT}.company_bank_map")

T = "silver.company_bank_map"
dq_unique(company_map, T, ["company"])
dq_check(
    T,
    "manual override certs exist in FDIC institutions",
    overrides.join(inst, overrides.cert_override == inst.cert, "left_anti").count(),
)
dq_check(
    T,
    "manual override companies present in complaints",
    overrides.join(companies, "company", "left_anti").count(),
    level="warn",
)

display(
    company_map.groupBy("match_method")
    .agg(F.count("*").alias("companies"), F.sum("complaints").alias("complaints"))
    .orderBy(F.col("complaints").desc())
)
display(company_map.orderBy(F.col("complaints").desc()).limit(40))

# COMMAND ----------

# Matches for new complaints, and for old ones whose company's match changed; others untouched.
LOADS["cfpb_complaints"]["rematched"] = rematch(f"{OUT}.cfpb_complaints", company_map)
comp = spark.table(f"{OUT}.cfpb_complaints")
n_comp = comp.count()
print(f"{OUT}.cfpb_complaints: {LOADS['cfpb_complaints']['rematched']} complaints (re)matched, {n_comp} rows")

T = "silver.cfpb_complaints"
dq_not_null(comp, T, ["complaint_id", "date_received", "company"])
dq_unique(comp, T, ["complaint_id"])
dq_check(
    T,
    "no complaints lost in de-dup/join",
    abs(spark.table(f"{BRONZE}.cfpb_complaints").select("complaint_id").distinct().count() - n_comp),
)
dq_check(
    T,
    "credit bureaus excluded",
    comp.filter(F.upper("company").rlike("EQUIFAX|TRANSUNION|EXPERIAN INFORMATION")).count(),
)
dq_check(
    T,
    "date_received in 2011-12-01..today",
    comp.filter(
        (F.col("date_received") < "2011-12-01") | (F.col("date_received") > F.current_date())
    ).count(),
)
dq_check(T, "timely_response is Yes/No", comp.filter("timely_response is null").count(), level="warn")

# COMMAND ----------

dq_summary = dq_finish()
matched = comp.filter("cert is not null").count()
summary = {
    "rows": {
        t: spark.table(f"{OUT}.{t}").count()
        for t in (
            "fdic_financials",
            "fdic_institutions",
            "fdic_failures",
            "company_bank_map",
            "cfpb_complaints",
        )
    },
    "complaints_matched_to_bank_pct": round(100 * matched / n_comp, 1) if n_comp else None,
    "companies_matched": company_map.filter("cert is not null").count(),
    "silver_mode": SILVER_MODE,
    "loads": LOADS,  # per table: full or incremental, rows inserted/updated/rematched
    "data_quality": dq_summary,
}
print(json.dumps(summary, indent=2))
dbutils.notebook.exit(json.dumps(summary))
