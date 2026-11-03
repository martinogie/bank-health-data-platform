# Databricks notebook source
# MAGIC %md
# MAGIC # e01_match_sample — a labelled sample of the CFPB company → FDIC bank matches (E-01)
# MAGIC
# MAGIC Read-only. Reads `silver.company_bank_map`, `silver.cfpb_complaints` and
# MAGIC `silver.fdic_institutions` from `source_catalog` (prod `bank_health` by default, as agreed for
# MAGIC PR 6) and writes nothing. Returns, as JSON in the exit value:
# MAGIC
# MAGIC - **population:** companies and complaints by match method (what the sample stands for);
# MAGIC - **sample:** 100 matched and 100 unmatched companies, drawn at random with a fixed seed (the
# MAGIC   order of `xxhash64(seed, company)`, so the same table gives the same sample), each with
# MAGIC   evidence for labelling: complaint count, top states and products, the matched bank and its
# MAGIC   holding company, and for unmatched companies the closest FDIC bank or holding-company name.
# MAGIC
# MAGIC The closest name is only a **candidate** for a person to check (difflib similarity on a simple
# MAGIC normalisation); it never changes a match. `scripts/e01_match_sample.py` runs this and writes
# MAGIC `docs/investigations/e01_match_labels.csv`.

# COMMAND ----------

# MAGIC %run ../_common

# COMMAND ----------

import difflib
import re

dbutils.widgets.text("source_catalog", "bank_health", "Catalog to read (read-only)")
dbutils.widgets.text("seed", "20261029", "Sampling seed")
dbutils.widgets.text("per_group", "100", "Companies per group (matched, unmatched)")
SRC = dbutils.widgets.get("source_catalog").strip()
SEED = int(dbutils.widgets.get("seed"))
PER_GROUP = int(dbutils.widgets.get("per_group"))

cmap = spark.table(f"{SRC}.silver.company_bank_map")
comp = spark.table(f"{SRC}.silver.cfpb_complaints")
inst = spark.table(f"{SRC}.silver.fdic_institutions")

# COMMAND ----------

population = [
    r.asDict()
    for r in cmap.groupBy("match_method")
    .agg(F.count("*").alias("companies"), F.sum("complaints").alias("complaints"))
    .orderBy("match_method")
    .collect()
]
print(population)


def top_values(col, n):
    w = Window.partitionBy("company").orderBy(F.col("n").desc(), F.col(col))
    return (
        comp.filter(F.col(col).isNotNull())
        .groupBy("company", col)
        .agg(F.count("*").alias("n"))
        .withColumn("rank", F.row_number().over(w))
        .filter(F.col("rank") <= n)
        .groupBy("company")
        .agg(
            F.concat_ws(
                "; ", F.collect_list(F.concat(F.col(col), F.lit(" ("), F.col("n"), F.lit(")")))
            ).alias(f"top_{col}s")
        )
    )


sample = (
    cmap.withColumn("group", F.when(F.col("cert").isNotNull(), "matched").otherwise("unmatched"))
    .withColumn("draw", F.xxhash64(F.lit(SEED), F.col("company")))
    .withColumn("draw_rank", F.row_number().over(Window.partitionBy("group").orderBy("draw", "company")))
    .filter(F.col("draw_rank") <= PER_GROUP)
    .join(
        inst.select(
            "cert",
            F.col("holding_company").alias("matched_holding_company"),
            F.col("state_code").alias("matched_state"),
            F.col("city").alias("matched_city"),
            F.col("is_active").alias("matched_active"),
            F.col("total_assets_k").alias("matched_assets_k"),
        ),
        "cert",
        "left",
    )
    .join(top_values("state_code", 3), "company", "left")
    .join(top_values("product", 2), "company", "left")
    .orderBy("group", "draw_rank")
    .toPandas()
)

# COMMAND ----------

# Candidates for unmatched companies: the closest FDIC bank or holding-company name.
SUFFIXES = re.compile(
    r"\b(INCORPORATED|INC|CORPORATION|CORP|COMPANY|CO|LLC|L L C|LP|L P|LTD|THE|"
    r"NATIONAL ASSOCIATION|N A|NA|FSB|F S B|SSB|HOLDINGS|HOLDING)\b"
)


def simple_key(name):
    s = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper().replace("&", " AND "))
    s = SUFFIXES.sub(" ", s)
    return re.sub(r"\s+", "", s)


names = (
    inst.select("cert", "bank_name", "holding_company", "state_code", "is_active", "total_assets_k")
    .toPandas()
    .sort_values("total_assets_k", ascending=False)
)
choices = {}
for r in names.itertuples():
    for kind, value in (("bank", r.bank_name), ("holding company", r.holding_company)):
        if value:
            choices.setdefault(simple_key(value), (kind, value, r.cert, r.state_code, bool(r.is_active)))
keys = list(choices)


def best_candidate(company):
    key = simple_key(company)
    found = difflib.get_close_matches(key, keys, n=1, cutoff=0.0)
    if not found:
        return {}
    kind, value, cert, state, active = choices[found[0]]
    return {
        "candidate_name": value,
        "candidate_kind": kind,
        "candidate_cert": int(cert),
        "candidate_state": state,
        "candidate_active": active,
        "candidate_similarity": round(difflib.SequenceMatcher(None, key, found[0]).ratio(), 2),
    }


rows = []
for r in sample.to_dict("records"):
    out = {
        "group": r["group"],
        "draw_rank": int(r["draw_rank"]),
        "company": r["company"],
        "complaints": int(r["complaints"]),
        "match_method": r["match_method"],
        "cert": None if r["cert"] is None or r["cert"] != r["cert"] else int(r["cert"]),
        "matched_bank_name": r["matched_bank_name"],
        "matched_holding_company": r["matched_holding_company"],
        "matched_state": r["matched_state"],
        "matched_active": r["matched_active"] if r["group"] == "matched" else None,
        "matched_assets_k": r["matched_assets_k"],
        "top_states": r["top_state_codes"],
        "top_products": r["top_products"],
    }
    if r["group"] == "unmatched":
        out.update(best_candidate(r["company"]))
    rows.append({k: (None if isinstance(v, float) and v != v else v) for k, v in out.items()})

print(f"{len(rows)} sampled companies (seed {SEED}, {PER_GROUP} per group) from {SRC}")
dbutils.notebook.exit(
    json.dumps({"source_catalog": SRC, "seed": SEED, "population": population, "sample": rows}, default=str)
)
