# Databricks notebook source
# MAGIC %md
# MAGIC # _scoring_rules — risk bands and reason texts (used by 05_score)
# MAGIC
# MAGIC Plain Python and pandas, no Spark, so the unit tests in `ingestion/tests/test_scoring_rules.py`
# MAGIC run the same code offline. Loaded with `%run ./_scoring_rules`.
# MAGIC
# MAGIC Bands have absolute meanings: the share of banks in the band that entered distress within
# MAGIC 4 quarters, measured out of sample on the training years (2001–2019):
# MAGIC **High** ≥ 50%, **Elevated** 25–50%, **Watch** 10–25%, **Low** < 10%.
# MAGIC The probability cut-offs are stored as tags on each registered model version.

# COMMAND ----------

import math

import pandas as pd

HIGH, ELEVATED, WATCH, LOW = "High", "Elevated", "Watch", "Low"
ALREADY_IN_DISTRESS = "Already in distress"
NEW_BANK = "New bank – not scored"
FOREIGN_OFFICE = "Office of a foreign bank – not scored"
SCORED_BANDS = [HIGH, ELEVATED, WATCH, LOW]
ALL_BANDS = SCORED_BANDS + [ALREADY_IN_DISTRESS, NEW_BANK, FOREIGN_OFFICE]
BAND_TAGS = {HIGH: "band_cutoff_high", ELEVATED: "band_cutoff_elevated", WATCH: "band_cutoff_watch"}


def cutoffs_from_tags(tags: dict) -> dict:
    """{'High': 0.36, 'Elevated': ..., 'Watch': ...} from a model version's tags."""
    missing = [t for t in BAND_TAGS.values() if t not in tags]
    if missing:
        raise ValueError(f"model version has no band cut-off tags {missing}; set them with 04_train_model")
    cuts = {band: float(tags[tag]) for band, tag in BAND_TAGS.items()}
    if not (1 > cuts[HIGH] > cuts[ELEVATED] > cuts[WATCH] > 0):
        raise ValueError(f"band cut-offs must satisfy 1 > High > Elevated > Watch > 0, got {cuts}")
    return cuts


def assign_band(
    probability, is_foreign_office: bool, is_new_bank: bool, is_distressed: bool, cutoffs: dict
) -> str:
    """Not-scored groups first, then 'already in distress', then the probability bands."""
    if is_foreign_office:
        return FOREIGN_OFFICE
    if is_new_bank:
        return NEW_BANK
    if is_distressed:
        return ALREADY_IN_DISTRESS
    if probability is None or (isinstance(probability, float) and math.isnan(probability)):
        raise ValueError("a scored bank needs a probability")
    for band in (HIGH, ELEVATED, WATCH):
        if probability >= cutoffs[band]:
            return band
    return LOW


def prepare_features(df: pd.DataFrame, features: list) -> pd.DataFrame:
    """Model input: the feature columns as floats. Missing stays missing (NaN), never 0."""
    return df[features].astype("float64")


def _fmt(v) -> str:
    return f"{v:,.2f}"


# A "change" feature is cited as a reason only when it moved the worsening way (Martins, 3 Nov 2026):
# +1 = an increase is worse, -1 = a decrease is worse. The waterfall keeps both directions.
WORSENING_DIRECTION = {
    "noncurrent_change_1q_pp": +1,
    "noncurrent_change_4q_pp": +1,
    "tier1_change_4q_pp": -1,
    "roa_change_4q_pp": -1,
}


def worsened(feature: str, value) -> bool:
    """True unless `feature` is a change feature that didn't move the worsening way."""
    sign = WORSENING_DIRECTION.get(feature)
    return sign is None or (pd.notna(value) and sign * float(value) > 0)


def reason_texts(values: pd.Series, drops: pd.Series, medians: pd.Series, k: int = 3) -> list:
    """Top-k reasons as 'feature = value (median m)'.

    `drops[f]` = how much the probability falls if feature f is set to the median. Only features
    that raise the risk (drop > 0) and that the bank actually reports (value not missing) qualify,
    so a reason never shows a value that isn't reported.
    """
    usable = [
        f
        for f in drops.index
        if drops[f] > 0
        and pd.notna(values.get(f))
        and pd.notna(medians.get(f))
        and worsened(f, values.get(f))
    ]
    top = drops[usable].sort_values(ascending=False).head(k)
    texts = [f"{f} = {_fmt(values[f])} (median {_fmt(medians[f])})" for f in top.index]
    return texts + [None] * (k - len(texts))


# Reasons by SHAP (M-05): each feature's contribution to this bank's log-odds, from shap's
# TreeExplainer on the gradient-boosting model. Compared with the bank's asset-size-tier peers.
REASON_SHAP = "shap"
REASON_MEDIAN_FALLBACK = "median_fallback"  # the earlier method: set one feature to the median
REASON_METHODS = (REASON_SHAP, REASON_MEDIAN_FALLBACK)
SHAP_ADDITIVITY_TOLERANCE = 1e-6  # base + sum of contributions must equal the model's log-odds


def tier_peer_medians(X: pd.DataFrame, tiers: pd.Series) -> pd.DataFrame:
    """For each bank, the median of each feature among the banks in its asset-size tier (missing
    values ignored). Same index and columns as X."""
    return X.groupby(tiers.reindex(X.index)).transform("median")


def shap_reason_texts(
    values: pd.Series, contributions: pd.Series, peer_medians: pd.Series, k: int = 3
) -> list:
    """Top-k reasons as 'feature = value (peer median m)': the features that raise this bank's risk
    most (contribution > 0), largest first. A feature the bank doesn't report (missing value) is
    never cited, even if its missingness moved the score, and neither is one without a peer median."""
    usable = [
        f
        for f in contributions.index
        if contributions[f] > 0
        and pd.notna(values.get(f))
        and pd.notna(peer_medians.get(f))
        and worsened(f, values.get(f))
    ]
    top = contributions[usable].sort_values(ascending=False).head(k)
    texts = [f"{f} = {_fmt(values[f])} (peer median {_fmt(peer_medians[f])})" for f in top.index]
    return texts + [None] * (k - len(texts))


def check_additivity(base: float, contributions, log_odds) -> float:
    """Largest gap between base + sum of contributions and the model's log-odds; raises if it is
    above SHAP_ADDITIVITY_TOLERANCE (then the explanation doesn't describe this model)."""
    import numpy as np

    gap = (
        float(np.max(np.abs(base + np.asarray(contributions).sum(axis=1) - np.asarray(log_odds))))
        if len(log_odds)
        else 0.0
    )
    if gap > SHAP_ADDITIVITY_TOLERANCE:
        raise ValueError(f"SHAP values don't add up to the model's log-odds (max gap {gap:.2e})")
    return gap


# Waterfall for the website's Bank Detail page (PR 7): the 8 features that move this bank's score
# most, either way, as percentage points of probability. SHAP adds up in log-odds; the steps are
# turned into probability by walking from the base rate in order of size, so the bars plus
# "other" add up exactly to the bank's probability.
WATERFALL_TOP = 8


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def shap_waterfall(
    values: pd.Series, contributions: pd.Series, peer_medians: pd.Series, base: float, k: int = WATERFALL_TOP
) -> dict:
    """{"base_pct", "items": [{"f", "v", "m", "pp"} x k], "other_pp", "p_pct"}: f = feature, v = the
    bank's value (None if not reported), m = peer median, pp = push in percentage points."""
    order = contributions.abs().sort_values(ascending=False, kind="stable").index
    cum, items = base, []
    for f in order[:k]:
        before, cum = cum, cum + float(contributions[f])
        v, m = values.get(f), peer_medians.get(f)
        items.append(
            {
                "f": f,
                "v": None if pd.isna(v) else round(float(v), 4),
                "m": None if pd.isna(m) else round(float(m), 4),
                "pp": round(100 * (_sigmoid(cum) - _sigmoid(before)), 3),
            }
        )
    total = base + float(contributions.sum())
    return {
        "base_pct": round(100 * _sigmoid(base), 3),
        "items": items,
        "other_pp": round(100 * (_sigmoid(total) - _sigmoid(cum)), 3),
        "p_pct": round(100 * _sigmoid(total), 3),
    }


# Banks "Already in distress" get no reasons; instead, the distress lines they crossed this quarter
# (the same lines as the model's label, _features.py).
def distress_lines(
    ncl_pct, tier1_pct, loss_now, loss_prev, ncl_threshold: float, tier1_threshold: float
) -> list:
    out = []
    if pd.notna(ncl_pct) and ncl_pct > ncl_threshold:
        out.append(
            {"line": "noncurrent_loans_above", "value": round(float(ncl_pct), 2), "threshold": ncl_threshold}
        )
    if pd.notna(tier1_pct) and tier1_pct < tier1_threshold:
        out.append(
            {
                "line": "tier1_leverage_below",
                "value": round(float(tier1_pct), 2),
                "threshold": tier1_threshold,
            }
        )
    if bool(loss_now) is True and bool(loss_prev) is True and pd.notna(loss_now) and pd.notna(loss_prev):
        out.append({"line": "two_quarterly_losses", "value": None, "threshold": None})
    return out
