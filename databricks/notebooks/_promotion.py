# Databricks notebook source
# MAGIC %md
# MAGIC # _promotion — the promotion gate (M-09; used by 04_train_model)
# MAGIC
# MAGIC Plain Python, so `ingestion/tests/test_promotion.py` runs the same code offline.
# MAGIC
# MAGIC A retrained model may become `@champion` only if it is **not worse** than the current champion
# MAGIC beyond the tolerances (decision 4, 29 Oct 2026), judged on **the same test rows** (checked by a
# MAGIC hash of the row keys), **and** a person runs training with `promote=true`:
# MAGIC
# MAGIC | Result | When | Promotion with `promote=true` |
# MAGIC |---|---|---|
# MAGIC | `fail` | PR-AUC more than 0.005 below the champion, or top-10% capture more than 1.0 point below; or the test rows differ | refused |
# MAGIC | `pass` | not a fail, and better beyond a tolerance on PR-AUC or capture | allowed |
# MAGIC | `tie` | within both tolerances | allowed (not worse) |
# MAGIC | `no_champion` | nothing to compare with (first model) | allowed |

# COMMAND ----------

import hashlib

PR_AUC_TOLERANCE = 0.005
CAPTURE_TOLERANCE = 0.01  # 1.0 percentage point of top-10% capture
PASS, FAIL, TIE, NO_CHAMPION = "pass", "fail", "tie", "no_champion"


def rows_hash(keys) -> str:
    """Order-independent fingerprint of the test rows: sha256 of the sorted 'cert|report_date' keys."""
    joined = "\n".join(sorted(f"{int(c)}|{d}" for c, d in keys))
    return hashlib.sha256(joined.encode()).hexdigest()


def gate(new: dict, champion: dict | None, new_rows: str, champion_rows: str | None) -> dict:
    """The decision, with the reasons. `new` and `champion` hold pr_auc and top_decile_capture on
    the test rows; the *_rows are rows_hash() of the rows each was judged on."""
    if champion is None:
        return {"decision": NO_CHAMPION, "reasons": ["no champion to compare with"], "promotable": True}
    d_pr = new["pr_auc"] - champion["pr_auc"]
    d_cap = new["top_decile_capture"] - champion["top_decile_capture"]
    out = {"delta_pr_auc": round(d_pr, 4), "delta_top_decile_capture": round(d_cap, 4)}
    reasons = []
    if new_rows != champion_rows:
        reasons.append("the two models were not judged on the same test rows (row hashes differ)")
    if d_pr < -PR_AUC_TOLERANCE:
        reasons.append(f"PR-AUC {d_pr:+.4f} vs the champion (tolerance -{PR_AUC_TOLERANCE})")
    if d_cap < -CAPTURE_TOLERANCE:
        reasons.append(
            f"top-10% capture {100 * d_cap:+.1f} points vs the champion (tolerance -{100 * CAPTURE_TOLERANCE:.1f})"
        )
    if reasons:
        return {**out, "decision": FAIL, "reasons": reasons, "promotable": False}
    better = d_pr > PR_AUC_TOLERANCE or d_cap > CAPTURE_TOLERANCE
    reason = (
        "better than the champion beyond a tolerance" if better else "within both tolerances of the champion"
    )
    return {**out, "decision": PASS if better else TIE, "reasons": [reason], "promotable": True}
