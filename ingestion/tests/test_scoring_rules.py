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


# --------------------------------------------------------------- SHAP reasons (M-05)
def test_shap_reasons_rank_by_contribution_against_tier_peers():
    values = pd.Series({"noncurrent_loans_pct": 4.2, "roa_pct": -0.5, "tier1_leverage_pct": 7.0})
    contrib = pd.Series({"noncurrent_loans_pct": 0.8, "roa_pct": 1.1, "tier1_leverage_pct": -0.3})
    peers = pd.Series({"noncurrent_loans_pct": 0.5, "roa_pct": 1.0, "tier1_leverage_pct": 10.0})
    assert R["shap_reason_texts"](values, contrib, peers) == [
        "roa_pct = -0.50 (peer median 1.00)",
        "noncurrent_loans_pct = 4.20 (peer median 0.50)",
        None,  # tier 1 lowers this bank's risk: never a reason
    ]


def test_shap_reasons_never_cite_a_missing_value_even_if_missingness_moved_the_score():
    values = pd.Series({"brokered_to_deposits_pct": np.nan, "roa_pct": -0.2, "cre_to_equity_pct": 400.0})
    contrib = pd.Series({"brokered_to_deposits_pct": 2.0, "roa_pct": 0.4, "cre_to_equity_pct": 0.3})
    peers = pd.Series({"brokered_to_deposits_pct": 1.0, "roa_pct": 1.0, "cre_to_equity_pct": np.nan})
    texts = R["shap_reason_texts"](values, contrib, peers)
    assert texts == ["roa_pct = -0.20 (peer median 1.00)", None, None]  # no peer median for CRE: not cited


def test_tier_peer_medians_use_each_banks_own_tier():
    X = pd.DataFrame({"roa_pct": [1.0, 3.0, 10.0, 20.0, np.nan]}, index=[10, 11, 12, 13, 14])
    tiers = pd.Series(["small", "small", "large", "large", "large"], index=[10, 11, 12, 13, 14])
    peers = R["tier_peer_medians"](X, tiers)
    assert peers["roa_pct"].tolist() == [2.0, 2.0, 15.0, 15.0, 15.0]  # missing values ignored


def test_additivity_check_passes_on_exact_values_and_refuses_a_mismatch():
    contrib = np.array([[0.5, -0.25], [1.0, 0.0]])
    assert R["check_additivity"](-2.0, contrib, np.array([-1.75, -1.0])) == 0.0
    with pytest.raises(ValueError, match="don't add up"):
        R["check_additivity"](-2.0, contrib, np.array([-1.75, -0.5]))


def test_shap_reasons_still_pass_the_top20_capital_check():
    from ingestion import sanity_checks

    zero = "tier1_leverage_pct = 0.00 (peer median 10.50)"
    assert sanity_checks._ZERO_CAPITAL_REASON.search(zero)
    assert not sanity_checks._ZERO_CAPITAL_REASON.search("tier1_leverage_pct = 6.10 (peer median 10.50)")


# --------------------------------------------------------------- waterfall and distress lines (PR 7)
def test_waterfall_bars_add_up_to_the_banks_probability():
    contrib = pd.Series(
        {f"f{i}": c for i, c in enumerate([1.2, -0.8, 0.5, 0.3, -0.2, 0.15, 0.1, 0.05, 0.04, -0.03])}
    )
    values = pd.Series({f: 1.0 for f in contrib.index} | {"f1": np.nan})
    peers = pd.Series({f: 0.5 for f in contrib.index})
    base = -2.5
    w = R["shap_waterfall"](values, contrib, peers, base)
    p = 1 / (1 + math.exp(-(base + contrib.sum())))
    assert [i["f"] for i in w["items"]] == [
        "f0",
        "f1",
        "f2",
        "f3",
        "f4",
        "f5",
        "f6",
        "f7",
    ]  # by size, either way
    assert len(w["items"]) == 8 and w["items"][1]["pp"] < 0 and w["items"][1]["v"] is None
    assert abs(w["base_pct"] + sum(i["pp"] for i in w["items"]) + w["other_pp"] - w["p_pct"]) < 0.01
    assert abs(w["p_pct"] - 100 * p) < 1e-3  # stored to 3 decimals


def test_distress_lines_name_each_line_crossed():
    lines = R["distress_lines"](4.2, 4.1, True, True, 3.0, 5.0)
    assert [d["line"] for d in lines] == [
        "noncurrent_loans_above",
        "tier1_leverage_below",
        "two_quarterly_losses",
    ]
    assert lines[0] == {"line": "noncurrent_loans_above", "value": 4.2, "threshold": 3.0}
    assert R["distress_lines"](3.0, 5.0, True, False, 3.0, 5.0) == []  # at the line is not over it
    assert R["distress_lines"](np.nan, None, None, True, 3.0, 5.0) == []  # missing: no breach


def test_change_features_are_cited_only_when_they_worsened():
    # Pinnacle Bank's case: non-current loans fell 1.45 pp over the quarter, yet raised the score
    values = pd.Series(
        {
            "noncurrent_change_1q_pp": -1.45,
            "roa_change_4q_pp": -0.4,
            "tier1_change_4q_pp": 0.8,
            "roa_pct": 0.2,
        }
    )
    contrib = pd.Series(
        {"noncurrent_change_1q_pp": 0.9, "roa_change_4q_pp": 0.5, "tier1_change_4q_pp": 0.4, "roa_pct": 0.3}
    )
    peers = pd.Series({f: 0.0 for f in values.index})
    texts = R["shap_reason_texts"](values, contrib, peers)
    assert texts == ["roa_change_4q_pp = -0.40 (peer median 0.00)", "roa_pct = 0.20 (peer median 0.00)", None]
    assert (
        R["reason_texts"](values, contrib, peers)[0] == "roa_change_4q_pp = -0.40 (median 0.00)"
    )  # fallback too
    assert R["worsened"]("noncurrent_change_4q_pp", 0.3) and not R["worsened"]("tier1_change_4q_pp", 0.3)
