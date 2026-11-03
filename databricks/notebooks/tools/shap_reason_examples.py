# Databricks notebook source
# MAGIC %md
# MAGIC # shap_reason_examples — old vs new reasons, for review (M-05, PR 7)
# MAGIC
# MAGIC Read-only: reads prod gold and failures, and the `@champion` model; writes nothing. For each
# MAGIC example, the reasons the earlier median method gives and the reasons SHAP gives (the code in
# MAGIC `_scoring_rules`, as `05_score` uses it), side by side:
# MAGIC
# MAGIC - **today's top 20** scored banks (latest quarter);
# MAGIC - **10 known failures from 2009–2010** (closures only; the largest by assets), scored on their call report
# MAGIC   4 quarters before failure. **In-sample**: v5 was trained on 2001–2019, so these banks were
# MAGIC   in its training data; they show how reasons read, not how well the model predicts.
# MAGIC
# MAGIC Medians and peer medians are those of the same quarter (all scored banks; the bank's
# MAGIC asset-size tier), as in scoring.

# COMMAND ----------

# MAGIC %pip install --quiet "mlflow==2.22.5" "scikit-learn==1.5.2" "shap==0.46.0" "numba==0.60.0" "llvmlite==0.43.0"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ../_common

# COMMAND ----------

# MAGIC %run ../_features

# COMMAND ----------

# MAGIC %run ../_scoring_rules

# COMMAND ----------

import mlflow
import pandas as pd
import shap

PROD = ENV_CATALOGS["prod"]
CLOSURE_TYPES = [
    "PA",
    "P&A",
    "PO",
    "PI",
    "IDT",
    "DINB",
]  # purchase and assumption, payout, deposit transfer, DINB
GOLD = f"{PROD}.gold"  # build_model_frame reads {GOLD}.bank_quarter: prod's full history, read-only
mlflow.set_registry_uri("databricks-uc")
model = mlflow.sklearn.load_model(f"models:/{CHAMPION_MODEL}@champion")
features = list(model.feature_names_in_)
explainer = shap.TreeExplainer(model)

frame = build_model_frame().withColumn("q_idx", F.year("report_date") * 4 + F.quarter("report_date"))
failures = (
    spark.table(f"{PROD}.silver.fdic_failures")
    .filter(F.col("fail_date").between("2009-01-01", "2010-12-31"))
    # closures only: the FDIC list also holds assistance transactions (OBAM, A/A, ...), e.g. the
    # open-bank assistance to Bank of America's banks on 16 Jan 2009, which are not failures
    .filter(F.col("resolution_type").isin(CLOSURE_TYPES))
    .withColumn("fail_q_idx", F.year("fail_date") * 4 + F.quarter("fail_date"))
)
picked = (
    failures.join(frame.select("cert", "q_idx", "report_date"), "cert")
    .filter(F.col("q_idx") == F.col("fail_q_idx") - 4)
    .select(
        "cert", F.col("bank_name").alias("failed_bank"), "fail_date", "report_date", failures.total_assets_k
    )
    .orderBy(F.col("total_assets_k").desc())
    .limit(10)
    .toPandas()
)
latest = frame.agg(F.max("report_date")).first()[0]


def explain_quarter(report_date, certs=None, top_n=None):
    """Old and new reasons for banks of one quarter, against that quarter's scored banks."""
    q = frame.filter(F.col("report_date") == report_date).toPandas()
    q = q[~q["is_foreign_office"] & ~q["is_new_bank"]].set_index("cert", drop=False)
    X = prepare_features(q, features)
    q["p"] = model.predict_proba(X)[:, 1]
    pool = q[~q["is_distressed"]]  # the scored banks of that quarter: the comparison group
    medians = prepare_features(pool, features).median()
    peers = tier_peer_medians(prepare_features(pool, features), pool["asset_size_tier"])
    peer_by_tier = pool[["asset_size_tier"]].join(peers).groupby("asset_size_tier").first()
    if top_n:
        chosen = pool.sort_values("p", ascending=False).head(top_n)
    else:
        chosen = q.loc[[c for c in certs if c in q.index]]
    Xc = prepare_features(chosen, features)
    sv = explainer.shap_values(Xc)
    sv = sv[1] if isinstance(sv, list) else sv
    contrib = pd.DataFrame(sv, index=Xc.index, columns=features)
    rows = []
    for cert, r in chosen.iterrows():
        drops = pd.Series(
            {
                f: r["p"] - model.predict_proba(Xc.loc[[cert]].assign(**{f: medians[f]}))[:, 1][0]
                for f in features
            }
        )
        tier_peers = (
            peer_by_tier.loc[r["asset_size_tier"]]
            if r["asset_size_tier"] in peer_by_tier.index
            else pd.Series(dtype=float)
        )
        rows.append(
            {
                "cert": int(cert),
                "bank": r["bank_name"],
                "state": r["state_code"],
                "tier": r["asset_size_tier"],
                "report_date": str(report_date),
                "probability": round(float(r["p"]), 4),
                "old": [t for t in reason_texts(Xc.loc[cert], drops, medians) if t],
                "new": [t for t in shap_reason_texts(Xc.loc[cert], contrib.loc[cert], tier_peers) if t],
            }
        )
    return rows


top20 = explain_quarter(latest, top_n=20)
fail_rows = []
for rd, grp in picked.groupby("report_date"):
    for row in explain_quarter(rd, certs=list(grp["cert"])):
        f = grp[grp["cert"] == row["cert"]].iloc[0]
        fail_rows.append({**row, "fail_date": str(f["fail_date"]), "in_sample": True})
fail_rows.sort(key=lambda r: -float(picked.set_index("cert").loc[r["cert"], "total_assets_k"]))
result = {"latest_quarter": str(latest), "top20": top20, "failures_2009_2010": fail_rows}
print(f"{len(top20)} top-20 banks, {len(fail_rows)} failures")
dbutils.notebook.exit(json.dumps(result, default=str))
