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


def reason_texts(values: pd.Series, drops: pd.Series, medians: pd.Series, k: int = 3) -> list:
    """Top-k reasons as 'feature = value (median m)'.

    `drops[f]` = how much the probability falls if feature f is set to the median. Only features
    that raise the risk (drop > 0) and that the bank actually reports (value not missing) qualify,
    so a reason never shows a value that isn't reported.
    """
    usable = [f for f in drops.index if drops[f] > 0 and pd.notna(values.get(f)) and pd.notna(medians.get(f))]
    top = drops[usable].sort_values(ascending=False).head(k)
    texts = [f"{f} = {_fmt(values[f])} (median {_fmt(medians[f])})" for f in top.index]
    return texts + [None] * (k - len(texts))
