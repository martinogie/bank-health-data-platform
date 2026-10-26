"""Unit tests for databricks/notebooks/_scoring_rules.py (bands and reason texts).

The notebook is plain Python, so it is loaded here directly: the tests run the same code that
05_score runs on Databricks, offline and without Spark.
"""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

NOTEBOOK = Path(__file__).resolve().parents[2] / "databricks" / "notebooks" / "_scoring_rules.py"
FEATURES_NB = Path(__file__).resolve().parents[2] / "databricks" / "notebooks" / "_features.py"


def load_notebook(path):
    ns = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
    return ns


R = load_notebook(NOTEBOOK)
CUTS = {"High": 0.36, "Elevated": 0.12, "Watch": 0.04}


def band(p, foreign=False, new=False, distressed=False):
    return R["assign_band"](p, foreign, new, distressed, CUTS)


# ------------------------------------------------------------------ bands
def test_bands_follow_cutoffs():
    assert band(0.50) == "High"
    assert band(0.36) == "High"  # cut-off is inclusive
    assert band(0.20) == "Elevated"
    assert band(0.05) == "Watch"
    assert band(0.01) == "Low"


def test_new_bank_is_not_scored_whatever_its_probability():
    assert band(0.99, new=True) == "New bank – not scored"
    assert band(float("nan"), new=True) == "New bank – not scored"
    assert band(0.99, new=True, distressed=True) == "New bank – not scored"


def test_foreign_office_is_not_scored_and_wins_over_other_flags():
    assert band(None, foreign=True, new=True, distressed=True) == "Office of a foreign bank – not scored"


def test_already_in_distress():
    assert band(0.01, distressed=True) == "Already in distress"


def test_scored_bank_without_probability_is_an_error():
    with pytest.raises(ValueError):
        band(float("nan"))


def test_cutoffs_from_tags_reads_and_validates():
    tags = {"band_cutoff_high": "0.36", "band_cutoff_elevated": "0.12", "band_cutoff_watch": "0.04", "x": "y"}
    assert R["cutoffs_from_tags"](tags) == CUTS
    with pytest.raises(ValueError):
        R["cutoffs_from_tags"]({"band_cutoff_high": "0.36"})  # missing tags
    with pytest.raises(ValueError):
        R["cutoffs_from_tags"]({**tags, "band_cutoff_watch": "0.20"})  # Watch above Elevated


def test_all_bands_listed_once():
    assert len(set(R["ALL_BANDS"])) == len(R["ALL_BANDS"]) == 7


# ------------------------------------------------------------- features
def test_null_stays_null_in_model_input():
    df = pd.DataFrame({"tier1_leverage_pct": [9.5, None], "cre_to_loans_pct": [None, 12.0], "other": [1, 2]})
    X = R["prepare_features"](df, ["tier1_leverage_pct", "cre_to_loans_pct"])
    assert list(X.columns) == ["tier1_leverage_pct", "cre_to_loans_pct"]
    assert X.dtypes.eq("float64").all()
    assert math.isnan(X.at[1, "tier1_leverage_pct"]) and math.isnan(X.at[0, "cre_to_loans_pct"])
    assert (X.fillna(-1) != 0).all().all()  # nothing became 0


def test_risk_based_capital_is_not_a_model_feature():
    src = FEATURES_NB.read_text(encoding="utf-8")
    features_block = src.split("FEATURES = [", 1)[1].split("]", 1)[0]
    assert "total_risk_based_capital_pct" not in features_block


# --------------------------------------------------------------- reasons
def test_reasons_never_mention_a_missing_feature():
    values = pd.Series({"total_risk_based_capital_pct": np.nan, "noncurrent_loans_pct": 4.2, "roa_pct": -0.5})
    drops = pd.Series({"total_risk_based_capital_pct": 0.30, "noncurrent_loans_pct": 0.10, "roa_pct": 0.05})
    medians = pd.Series({"total_risk_based_capital_pct": 15.0, "noncurrent_loans_pct": 0.4, "roa_pct": 1.0})
    texts = R["reason_texts"](values, drops, medians)
    assert texts[0] == "noncurrent_loans_pct = 4.20 (median 0.40)"
    assert texts[1] == "roa_pct = -0.50 (median 1.00)"
    assert texts[2] is None
    assert not any("total_risk_based" in t for t in texts if t)


def test_reasons_only_for_features_that_raise_risk():
    values = pd.Series({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})
    drops = pd.Series({"a": -0.1, "b": 0.0, "c": 0.2, "d": 0.3})
    medians = pd.Series({"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0})
    assert R["reason_texts"](values, drops, medians) == [
        "d = 4.00 (median 0.00)",
        "c = 3.00 (median 0.00)",
        None,
    ]
