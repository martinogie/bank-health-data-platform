# Databricks notebook source
# MAGIC %md
# MAGIC # e01_match_whatif — before/after for the proposed matching changes (E-01)
# MAGIC
# MAGIC Read-only: reads prod silver (`source_catalog`), writes nothing, changes no match. Rebuilds the
# MAGIC company → bank map the way `02_silver` does, then with each proposal, and compares:
# MAGIC
# MAGIC - **baseline:** today's rules (checked to reproduce `silver.company_bank_map` exactly);
# MAGIC - **overrides:** 3 proposed manual matches and 6 proposed removals;
# MAGIC - **abbreviations:** more FDIC abbreviations expanded in the name normalisation (both sides);
# MAGIC - **sense check:** holding-company matches to small banks whose complaints look like a
# MAGIC   non-bank's and come from elsewhere are flagged for review (two variants);
# MAGIC - **ambiguity:** companies whose name matches one bank and whose holding-company name matches
# MAGIC   another (how Independent Bank Corp. went wrong).
# MAGIC
# MAGIC The matching code here is a copy of `02_silver`'s, for comparison only; an approved change
# MAGIC goes into `02_silver` in its own pull request.

# COMMAND ----------

# MAGIC %run ../_common

# COMMAND ----------

dbutils.widgets.text("source_catalog", "bank_health", "Catalog to read (read-only)")
SRC = dbutils.widgets.get("source_catalog").strip()
comp = spark.table(f"{SRC}.silver.cfpb_complaints")
inst = spark.table(f"{SRC}.silver.fdic_institutions")
prod_map = spark.table(f"{SRC}.silver.company_bank_map")

TODAY_OVERRIDES = [
    ("TD BANK US HOLDING COMPANY", 18409, "today"),
    ("FIFTH THIRD FINANCIAL CORPORATION", 6672, "today"),
    ("NEW YORK COMMUNITY BANCORP INC", 32541, "today"),
    ("SANTANDER HOLDINGS USA, INC.", 29950, "today"),
]
PROPOSED_OVERRIDES = [
    ("Bread Financial Holdings, Inc.", 57570, "Comenity Capital Bank; FDIC lists no holding company"),
    (
        "UNITED SERVICES AUTOMOBILE ASSOCIATION",
        32188,
        "USAA Federal Savings Bank; FDIC HC 'UNITED SERVICES AUTOMOBILE ASSN'",
    ),
    ("INDEPENDENT BANK CORP.", 9712, "Rockland Trust Company (MA); FDIC HC INDEPENDENT BANK CORP"),
]
PROPOSED_REMOVALS = [
    "Cornerstone Holdings, Inc.",
    "FIRST AMERICAN FINANCIAL CORPORATION",
    "Eagle Financial Services, Inc.",
    "Covington Capital Corporation",
    "Woodlands Financial Services Inc",
    "Town Financial Corporation",
]
TODAY_ABBREVIATIONS = [
    ("FINL", "FINANCIAL"),
    ("SVCS", "SERVICES"),
    ("BANCORPORATION", "BANCORP"),
    ("BCORP", "BANCORP"),
]
MORE_ABBREVIATIONS = [
    ("ASSN", "ASSOCIATION"), ("NATL", "NATIONAL"), ("BKG", "BANKING"), ("BK", "BANK"),
    ("BCSHS", "BANCSHARES"), ("BANCSHS", "BANCSHARES"), ("SVGS", "SAVINGS"), ("SVC", "SERVICES"),
    ("GRP", "GROUP"), ("INTL", "INTERNATIONAL"), ("AMER", "AMERICAN"), ("CMNTY", "COMMUNITY"),
    ("FED", "FEDERAL"), ("MUT", "MUTUAL"), ("HLDGS", "HOLDINGS"), ("HLDG", "HOLDING"), ("MGMT", "MANAGEMENT"),
]  # fmt: skip
LEGAL_SUFFIXES = (
    r"\b(INCORPORATED|INC|CORPORATION|CORP|COMPANY|CO|LLC|L L C|LP|L P|LTD|THE|"
    r"NATIONAL ASSOCIATION|N A|NA|FSB|F S B|SSB|HOLDINGS|HOLDING)\b"
)
NON_BANK_STRICT = ["Debt collection", "Payday loan, title loan, personal loan, or advance loan"]
NON_BANK_BROAD = NON_BANK_STRICT + [
    "Credit reporting or other personal consumer reports",
    "Debt or credit management",
]
SMALL_BANK_K = 10_000_000  # $10B in $ thousands
HOME_STATE_MAX = 0.25


def normalise(col_name, abbreviations):
    c = F.upper(F.col(col_name))
    c = F.regexp_replace(c, "&", " AND ")
    c = F.regexp_replace(c, "[^A-Z0-9 ]", " ")
    for short, full in abbreviations:
        c = F.regexp_replace(c, rf"\b{short}\b", full)
    c = F.regexp_replace(c, LEGAL_SUFFIXES, " ")
    c = F.regexp_replace(c, r"\s+", "")
    return F.when(c == "", None).otherwise(c)


complaint_start = comp.agg(F.min("date_received")).first()[0]
candidates = inst.filter(F.col("is_active") | (F.col("end_date") >= F.lit(complaint_start)))
companies = (
    comp.groupBy("company")
    .agg(F.count("*").alias("complaints"))
    .withColumn("is_llc_or_lp", F.upper("company").rlike(r"\b(LLC|L\.L\.C|LP|L\.P)\b"))
)


def build_map(abbreviations, overrides, removals=()):
    by_name = latest_per_key(
        candidates.withColumn("company_key", normalise("bank_name", abbreviations)),
        ["company_key"],
        ["total_assets_k"],
    ).select("company_key", F.col("cert").alias("cert_by_name"))
    by_hc = latest_per_key(
        candidates.filter("holding_company is not null").withColumn(
            "company_key", normalise("holding_company", abbreviations)
        ),
        ["company_key"],
        ["total_assets_k"],
    ).select("company_key", F.col("cert").alias("cert_by_hc"))
    ov = spark.createDataFrame(overrides, "company string, cert_override int, note string")
    m = (
        companies.withColumn("company_key", normalise("company", abbreviations))
        .join(ov, "company", "left")
        .join(by_name, "company_key", "left")
        .join(by_hc, "company_key", "left")
        .withColumn("cert_by_hc", F.when(~F.col("is_llc_or_lp"), F.col("cert_by_hc")))
        .withColumn("cert", F.coalesce("cert_override", "cert_by_name", "cert_by_hc"))
        .withColumn(
            "match_method",
            F.when(F.col("cert_override").isNotNull(), "manual_override")
            .when(F.col("cert_by_name").isNotNull(), "bank_name")
            .when(F.col("cert_by_hc").isNotNull(), "holding_company")
            .otherwise("unmatched"),
        )
    )
    if removals:
        gone = F.col("company").isin(list(removals))
        m = m.withColumn("cert", F.when(gone, None).otherwise(F.col("cert"))).withColumn(
            "match_method", F.when(gone, "removed_by_review").otherwise(F.col("match_method"))
        )
    return m.select("company", "complaints", "cert", "match_method", "cert_by_name", "cert_by_hc")


def totals(m):
    r = m.agg(
        F.sum(F.when(F.col("cert").isNotNull(), 1).otherwise(0)).alias("companies_matched"),
        F.sum(F.when(F.col("cert").isNotNull(), F.col("complaints")).otherwise(0)).alias(
            "complaints_matched"
        ),
        F.sum("complaints").alias("complaints"),
    ).first()
    return {
        "companies_matched": int(r["companies_matched"]),
        "complaints_matched": int(r["complaints_matched"]),
        "match_rate_pct": round(100 * r["complaints_matched"] / r["complaints"], 2),
    }


names = inst.select("cert", "bank_name", "state_code", "total_assets_k")


def changes(before, after):
    j = before.select(
        "company",
        "complaints",
        F.col("cert").alias("cert_before"),
        F.col("match_method").alias("method_before"),
    ).join(
        after.select(
            "company", F.col("cert").alias("cert_after"), F.col("match_method").alias("method_after")
        ),
        "company",
    )
    diff = j.filter(~F.col("cert_before").eqNullSafe(F.col("cert_after")))
    diff = diff.join(
        names.select(F.col("cert").alias("cert_before"), F.col("bank_name").alias("bank_before")),
        "cert_before",
        "left",
    )
    diff = diff.join(
        names.select(
            F.col("cert").alias("cert_after"),
            F.col("bank_name").alias("bank_after"),
            F.col("state_code").alias("state_after"),
        ),
        "cert_after",
        "left",
    )
    return [r.asDict() for r in diff.orderBy(F.col("complaints").desc()).collect()]


base = build_map(TODAY_ABBREVIATIONS, TODAY_OVERRIDES)
same_as_prod = (
    base.select("company", "cert").exceptAll(prod_map.select("company", "cert")).count() == 0
    and prod_map.select("company", "cert").exceptAll(base.select("company", "cert")).count() == 0
)
print("baseline reproduces silver.company_bank_map:", same_as_prod)
abbr = build_map(TODAY_ABBREVIATIONS + MORE_ABBREVIATIONS, TODAY_OVERRIDES)
over = build_map(TODAY_ABBREVIATIONS, TODAY_OVERRIDES + PROPOSED_OVERRIDES, PROPOSED_REMOVALS)
both = build_map(
    TODAY_ABBREVIATIONS + MORE_ABBREVIATIONS, TODAY_OVERRIDES + PROPOSED_OVERRIDES, PROPOSED_REMOVALS
)

# COMMAND ----------

# Sense check on holding-company matches (today's map): small bank, non-bank-looking complaints,
# few complaints from the bank's home state (FDIC gives one home state per bank; branch states
# would be better, but aren't in the pipeline).
prod_cnt = comp.groupBy("company").agg(F.count("*").alias("n"))
by_product = comp.groupBy("company").pivot("product").count().na.fill(0)
home = base.filter("match_method = 'holding_company'").join(names, "cert").join(prod_cnt, "company")
home_share = (
    comp.join(home.select("company", F.col("state_code").alias("home")), "company")
    .groupBy("company")
    .agg(
        (F.sum(F.when(F.col("state_code") == F.col("home"), 1).otherwise(0)) / F.count("*")).alias(
            "home_state_share"
        )
    )
)
hc = home.join(home_share, "company").join(by_product, "company", "left")


def share(cols):
    present = [c for c in cols if c in hc.columns]
    return sum((F.col(f"`{c}`") for c in present), F.lit(0)) / F.col("n")


hc = hc.withColumn("non_bank_strict", share(NON_BANK_STRICT)).withColumn(
    "non_bank_broad", share(NON_BANK_BROAD)
)
small = F.col("total_assets_k") < SMALL_BANK_K
far = F.col("home_state_share") < HOME_STATE_MAX
flags = {}
for variant in ("strict", "broad"):
    flagged = hc.filter(small & far & (F.col(f"non_bank_{variant}") >= 0.5))
    flags[variant] = [
        {
            "company": r["company"],
            "complaints": int(r["n"]),
            "bank": r["bank_name"],
            "bank_state": r["state_code"],
            "assets_k": r["total_assets_k"],
            "home_state_share": round(r["home_state_share"], 2),
            "non_bank_share": round(r[f"non_bank_{variant}"], 2),
        }
        for r in flagged.orderBy(F.col("n").desc()).collect()
    ]
hc_small = hc.filter(small).count()

# Ambiguity: the name matches one bank and the holding-company name another.
ambiguous = [
    r.asDict()
    for r in base.filter("cert_by_name is not null and cert_by_hc is not null and cert_by_name <> cert_by_hc")
    .join(
        names.select(F.col("cert").alias("cert_by_name"), F.col("bank_name").alias("name_bank")),
        "cert_by_name",
    )
    .join(names.select(F.col("cert").alias("cert_by_hc"), F.col("bank_name").alias("hc_bank")), "cert_by_hc")
    .select("company", "complaints", "cert_by_name", "name_bank", "cert_by_hc", "hc_bank")
    .orderBy(F.col("complaints").desc())
    .collect()
]

result = {
    "source_catalog": SRC,
    "baseline_reproduces_prod": same_as_prod,
    "totals": {
        "baseline": totals(base),
        "overrides": totals(over),
        "abbreviations": totals(abbr),
        "both": totals(both),
    },
    "changes": {"overrides": changes(base, over), "abbreviations": changes(base, abbr)},
    "sense_check": {
        "holding_company_matches": hc.count(),
        "to_banks_under_10b": hc_small,
        "flagged": flags,
        "rule": f"holding-company match, bank assets < $10B, home-state share < {HOME_STATE_MAX:.0%}, non-bank product share >= 50%",
    },
    "ambiguous_name_vs_hc": ambiguous,
}
print(json.dumps(result["totals"], indent=1))
dbutils.notebook.exit(json.dumps(result, default=str))
