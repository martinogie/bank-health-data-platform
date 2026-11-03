# Databricks notebook source
# MAGIC %md
# MAGIC # _monitoring — drift (PSI) and backtest rules for the model (M-08; used by 07_monitor)
# MAGIC
# MAGIC Plain Python, numpy and pandas, no Spark, so `ingestion/tests/test_monitoring.py` runs the
# MAGIC same code offline. Loaded with `%run ./_monitoring`.
# MAGIC
# MAGIC **PSI** (population stability index) compares a distribution with a reference: bins are the
# MAGIC reference's deciles (plus one bin for missing values); PSI = Σ (cur − ref) × ln(cur / ref).
# MAGIC Usual reading: < 0.10 stable, 0.10–0.25 a moderate shift, > 0.25 a large shift.
# MAGIC
# MAGIC Two comparisons for the latest quarter, for the score and for each model feature:
# MAGIC - **vs the previous 4 quarters** — the alert is on this one;
# MAGIC - **vs 2001–2019 training** — reported only (the world has moved since, by design).
# MAGIC
# MAGIC **Backtest:** for each quarter from 2020 whose outcome is known, the realised PR-AUC and
# MAGIC top-10% capture of the stored scores. A quarter is judged only with at least 30 positives.
# MAGIC A "sharp drop" is capture more than 10 points below the test result (52.6%, v5).

# COMMAND ----------

import math

import numpy as np

PSI_BINS = 10
PSI_EPS = 1e-4  # floor for an empty bin, so ln() stays finite
PSI_MODERATE = 0.10
PSI_LARGE = 0.25
FEATURES_LARGE_FOR_ALERT = 3  # this many features with a large shift (vs the previous 4 quarters) alert
TEST_CAPTURE = 0.526  # v5 top-10% capture on the 2020-2024 test rows (model card)
CAPTURE_MAX_DROP = 0.10
MIN_POSITIVES = 30
BACKTEST_FROM = "2020-01-01"
IN_SAMPLE_UNTIL = "2019-12-31"  # v5 trained on 2001-2019: rescored quarters up to here are in-sample


def psi(reference, current, bins: int = PSI_BINS) -> float | None:
    """Population stability index of `current` against `reference` (1-D arrays; NaN = missing,
    its own bin). Bin edges are the reference's quantiles, so each reference bin holds about
    1/bins of the non-missing rows. None if either side is empty."""
    ref = np.asarray(reference, dtype="float64")
    cur = np.asarray(current, dtype="float64")
    if ref.size == 0 or cur.size == 0:
        return None
    ref_ok, cur_ok = ref[~np.isnan(ref)], cur[~np.isnan(cur)]
    edges = np.unique(np.quantile(ref_ok, np.linspace(0, 1, bins + 1))) if ref_ok.size else np.array([])
    inner = edges[1:-1]  # open-ended outer bins: values beyond the reference range still count

    def shares(x, x_ok):
        counts = np.bincount(np.searchsorted(inner, x_ok, side="right"), minlength=len(inner) + 1)
        counts = np.append(counts, np.isnan(x).sum())  # the missing-value bin
        return np.maximum(counts / x.size, PSI_EPS)

    r, c = shares(ref, ref_ok), shares(cur, cur_ok)
    return float(np.sum((c - r) * np.log(c / r)))


def psi_level(value: float | None) -> str | None:
    if value is None:
        return None
    return "large" if value > PSI_LARGE else "moderate" if value > PSI_MODERATE else "stable"


def capture(y, p, share: float = 0.10) -> float:
    """Distress cases among the riskiest ceil(10%) rows, as a share of all cases (as in training)."""
    y = np.asarray(y)
    k = math.ceil(share * len(y))
    top = np.argsort(-np.asarray(p), kind="stable")[:k]
    return float(y[top].sum() / max(y.sum(), 1))


def backtest_row(report_date, y, p, pr_auc_fn) -> dict:
    """One quarter: rows, positives, PR-AUC and capture; judged only with MIN_POSITIVES positives."""
    y, p = np.asarray(y), np.asarray(p)
    positives = int(y.sum())
    judged = positives >= MIN_POSITIVES
    return {
        "report_date": str(report_date),
        "rows": int(len(y)),
        "positives": positives,
        "judged": judged,
        "pr_auc": round(float(pr_auc_fn(y, p)), 4) if positives and positives < len(y) else None,
        "top10_capture": round(capture(y, p), 4) if positives else None,
    }


def alerts(psi_rows: list, backtest_rows: list) -> list:
    """What would alert: a large score shift, or FEATURES_LARGE_FOR_ALERT+ features with a large
    shift, vs the previous 4 quarters; a judged quarter whose capture dropped sharply."""
    out = []
    recent = [r for r in psi_rows if r["reference"] == "previous_4q" and r["psi"] is not None]
    score = next((r for r in recent if r["name"] == "score"), None)
    if score and score["psi"] > PSI_LARGE:
        out.append(f"score PSI {score['psi']:.3f} vs the previous 4 quarters (large above {PSI_LARGE})")
    large = sorted(
        (r for r in recent if r["name"] != "score" and r["psi"] > PSI_LARGE), key=lambda r: -r["psi"]
    )
    if len(large) >= FEATURES_LARGE_FOR_ALERT:
        names = ", ".join(f"{r['name']} {r['psi']:.2f}" for r in large[:5])
        out.append(f"{len(large)} features with PSI above {PSI_LARGE} vs the previous 4 quarters: {names}")
    for b in backtest_rows:
        if (
            b["judged"]
            and b["top10_capture"] is not None
            and b["top10_capture"] < TEST_CAPTURE - CAPTURE_MAX_DROP
        ):
            out.append(
                f"{b['report_date']}: top-10% capture {b['top10_capture']:.1%}, more than "
                f"{100 * CAPTURE_MAX_DROP:.0f} points below the test result {TEST_CAPTURE:.1%}"
            )
    return out
