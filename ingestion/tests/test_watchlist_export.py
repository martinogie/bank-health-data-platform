import json
import math
import random
from datetime import date

import pytest

from ingestion import watchlist_export as wx

LATEST = date(2026, 6, 30)
QUARTERS = wx.quarter_ends(LATEST)


def bank_row(cert=628, **over):
    row = {
        "cert": cert,
        "bank_name": f"Bank {cert}",
        "city": "Columbus",
        "state_code": "OH",
        "asset_size_tier": "5: >$100B",
        "total_assets_k": 4_091_315_000.0,
        "distress_probability": 0.0057123,
        "risk_band": "Low",
        "already_in_distress": False,
        "reason_1": "noncurrent_loans_pct = 2.73 (median 0.46)",
        "reason_2": "tier1_change_4q_pp = -2.04 (median 0.19)",
        "reason_3": None,
        "complaints": 6834,
        "complaints_per_1b_deposits": 2.42317,
        "report_date": LATEST,
        "model_name": "bank_health.ml.distress_model",
        "model_version": 5,
        "established_date": date(1824, 1, 1),
        "has_cfpb_complaints": True,
        "peer_pct": 0.1049,
    }
    row.update(over)
    return row


def history_rows(cert=628):
    return [
        {
            "cert": cert,
            "report_date": q,
            "noncurrent_loans_pct": 0.5 + i / 100,
            "tier1_leverage_pct": 8.0,
            "roa_pct": 1.234567,
            "efficiency_ratio_pct": 55.0,
            "deposit_growth_yoy_pct": 3.0,
        }
        for i, q in enumerate(QUARTERS)
    ]


def peer_rows(tier="5: >$100B"):
    return [
        {
            "asset_size_tier": tier,
            "report_date": q,
            "noncurrent_loans_pct": 0.9,
            "tier1_leverage_pct": 9.0,
            "roa_pct": 1.1,
            "efficiency_ratio_pct": 60.0,
            "deposit_growth_yoy_pct": 2.0,
        }
        for q in QUARTERS
    ]


def payload(banks=None, hist=None, peers=None):
    return wx.build_payload(
        banks or [bank_row()],
        hist if hist is not None else history_rows(),
        peers if peers is not None else peer_rows(),
        date(2026, 10, 21),
    )


# ------------------------------------------------------------------ schema
def test_payload_matches_documented_schema():
    p = payload()
    wx.validate_payload(p)  # raises if anything is off
    assert set(p) == {"meta", "peers", "banks"}
    m = p["meta"]
    assert m["quarter"] == "2026-06-30" and m["quarterLabel"] == "2026 Q2"
    assert m["exported"] == "2026-10-21" and m["modelVersion"] == 5 and m["banks"] == 1
    assert m["history"] == [
        "2024 Q3",
        "2024 Q4",
        "2025 Q1",
        "2025 Q2",
        "2025 Q3",
        "2025 Q4",
        "2026 Q1",
        "2026 Q2",
    ]
    b = p["banks"][0]
    assert set(b) == {"c", "n", "ci", "st", "a", "age", "t", "p", "b", "pp", "r", "h", "cx", "cr"}
    assert set(b["h"]) == {"ncl", "t1", "roa", "eff", "dep"}
    assert set(p["peers"]) == {"5"} and len(p["peers"]["5"]["ncl"]) == 8


def test_payload_is_json_serialisable_and_compact(tmp_path):
    size = wx.write_payload(payload(), tmp_path / "w.json")
    text = (tmp_path / "w.json").read_text(encoding="utf-8")
    assert size == len(text.encode("utf-8"))
    data = json.loads(text)
    assert text == json.dumps(data, ensure_ascii=False, separators=(",", ":"))  # no padding anywhere
    assert data["meta"]["banks"] == 1
    assert not (tmp_path / "w.json.tmp").exists()


@pytest.mark.parametrize(
    "breaker, message",
    [
        (lambda p: p.pop("peers"), "top-level"),
        (lambda p: p["meta"].pop("modelVersion"), "modelVersion"),
        (lambda p: p["banks"][0]["h"]["ncl"].pop(), "history"),
        (lambda p: p["banks"][0].update(b="X"), "unknown band"),
        (lambda p: p["banks"][0].update(p=1.5), "probability"),
        (lambda p: (p["banks"].append(dict(p["banks"][0])), p["meta"].update(banks=2)), "duplicated"),
    ],
)
def test_validate_rejects_broken_payloads(breaker, message):
    p = payload()
    breaker(p)
    with pytest.raises(ValueError, match=message):
        wx.validate_payload(p)


def test_write_refuses_files_over_the_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(wx, "MAX_BYTES", 100)
    with pytest.raises(ValueError, match="over the"):
        wx.write_payload(payload(), tmp_path / "w.json")
    assert not (tmp_path / "w.json").exists()


# ----------------------------------------------------------------- rounding
@pytest.mark.parametrize(
    "value, digits, expected",
    [
        (0.0057123, 3, 0.006),
        (1.234567, 2, 1.23),
        (6834.0, 0, 6834),
        (None, 2, None),
        (float("nan"), 2, None),
        (float("inf"), 2, None),
        ("abc", 2, None),
    ],
)
def test_round_or_none(value, digits, expected):
    out = wx.round_or_none(value, digits)
    assert out == expected
    if digits == 0 and expected is not None:
        assert isinstance(out, int)


def test_bank_record_rounding():
    b = payload()["banks"][0]
    assert b["p"] == 0.006  # probability: 3 decimals
    assert b["a"] == 4_091_315.0  # $ thousands -> $ millions, 1 decimal
    assert b["pp"] == 10  # peer percentile: whole percent
    assert b["cr"] == 2.42  # complaints per $1bn: 2 decimals
    assert b["h"]["roa"][0] == 1.23  # history: 2 decimals


# ------------------------------------------------------- bank age, not-scored bands
@pytest.mark.parametrize(
    "established, expected_age",
    [
        (date(2024, 1, 1), 2.5),
        (date(1824, 1, 1), 202.5),
        (None, None),
        (date(2027, 1, 1), None),  # bad data (after the quarter): no age
    ],
)
def test_bank_age(established, expected_age):
    assert wx.bank_age_years(established, LATEST) == expected_age


def test_not_scored_bands_come_from_scoring_not_from_age():
    # 05_score decides who is not scored; the export only passes the band through
    p = payload(
        banks=[
            bank_row(
                1,
                established_date=date(2025, 12, 1),
                risk_band="New bank – not scored",
                distress_probability=None,
                peer_pct=None,
                reason_1=None,
                reason_2=None,
            ),
            bank_row(
                2,
                risk_band="Office of a foreign bank – not scored",
                distress_probability=None,
                peer_pct=None,
                reason_1=None,
                reason_2=None,
            ),
            bank_row(3, established_date=date(2025, 12, 1)),  # young but scored: stays scored
        ]
    )
    wx.validate_payload(p)
    by = {b["c"]: b for b in p["banks"]}
    assert (by[1]["b"], by[1]["p"]) == ("N", None)
    assert (by[2]["b"], by[2]["p"]) == ("F", None)
    assert by[3]["b"] == "L" and "nb" not in by[3]
    assert [b["c"] for b in p["banks"]] == [3, 1, 2]  # not scored last
    assert p["meta"]["bands"]["N"] == "New bank – not scored"


def test_unknown_band_is_an_error_not_low():
    with pytest.raises(ValueError, match="unknown risk band"):
        payload(banks=[bank_row(1, risk_band="Medium")])


def test_scored_bank_without_probability_is_rejected():
    p = payload()
    p["banks"][0]["p"] = None
    with pytest.raises(ValueError, match="no probability"):
        wx.validate_payload(p)


# ------------------------------------------------------------------ history
def test_quarter_ends_span_year_boundary_oldest_first():
    assert wx.quarter_ends(date(2026, 3, 31), 4) == [
        date(2025, 6, 30),
        date(2025, 9, 30),
        date(2025, 12, 31),
        date(2026, 3, 31),
    ]


def test_history_is_ordered_oldest_first_whatever_the_row_order():
    rows = history_rows()
    random.Random(7).shuffle(rows)
    h = wx.build_history(rows, QUARTERS)
    assert h["ncl"] == [0.5, 0.51, 0.52, 0.53, 0.54, 0.55, 0.56, 0.57]


def test_history_has_null_for_missing_quarters():
    rows = [r for r in history_rows() if r["report_date"] != QUARTERS[2]]
    h = wx.build_history(rows, QUARTERS)
    assert h["ncl"][2] is None and len(h["ncl"]) == 8
    assert wx.build_history([], QUARTERS)["t1"] == [None] * 8


def test_bank_without_history_still_valid():
    p = payload(hist=[])
    wx.validate_payload(p)
    assert p["banks"][0]["h"]["ncl"] == [None] * 8


# ------------------------------------------------------------ other fields
def test_reasons_are_plain_words():
    assert (
        wx.plain_reason("noncurrent_loans_pct = 2.73 (median 0.46)")
        == "Non-current loans: 2.73% of loans (median of all US banks: 0.46% of loans)"
    )
    assert (
        wx.plain_reason("tier1_change_4q_pp = -2.04 (median 0.19)")
        == "Change in Tier 1 leverage over the year: -2.04 pp (median of all US banks: +0.19 pp)"
    )
    assert (
        wx.plain_reason("efficiency_ratio_pct = 9,840.95 (median 61.97)")
        == "Efficiency ratio (costs per $1 of revenue): 9,841% (median of all US banks: 62.0%)"
    )
    assert (
        wx.plain_reason("roa_pct = n/a (median 1.22)")
        == "Return on assets: not reported (median of all US banks: 1.22%)"
    )
    size = wx.plain_reason(f"log_total_assets = {math.log(2.5e9):.2f} (median {math.log(3e8):.2f})")
    assert size.startswith("Size (total assets): $2.5bn (median of all US banks: $300m")
    assert wx.plain_reason(None) is None
    assert wx.plain_reason("something unexpected") == "something unexpected"


def test_reasons_compare_against_all_banks_not_typical_or_peers():
    # The reason's median is over ALL banks (05_score); the charts use size-tier peers.
    text = wx.plain_reason("roa_pct = -0.03 (median 1.22)")
    assert "median of all US banks" in text
    assert "typical" not in text and "peer" not in text


def test_missing_reasons_are_dropped():
    assert len(payload()["banks"][0]["r"]) == 2  # reason_3 was None


def test_complaints_only_for_matched_banks():
    p = payload(banks=[bank_row(1), bank_row(2, has_cfpb_complaints=False)])
    by = {b["c"]: b for b in p["banks"]}
    assert by[1]["cx"] == 6834 and "cr" in by[1]
    assert "cx" not in by[2] and "cr" not in by[2]


def test_already_distressed_banks_have_no_peer_percentile():
    p = payload(banks=[bank_row(1, risk_band="Already in distress", already_in_distress=True, peer_pct=None)])
    assert p["banks"][0]["b"] == "D" and p["banks"][0]["pp"] is None


def test_banks_sorted_by_probability_highest_first():
    p = payload(banks=[bank_row(1, distress_probability=0.1), bank_row(2, distress_probability=0.9)])
    assert [b["c"] for b in p["banks"]] == [2, 1]


def test_no_banks_is_an_error():
    with pytest.raises(ValueError, match="no scored banks"):
        wx.build_payload([], [], [], date(2026, 10, 21))


def test_plain_reason_says_which_median_a_reason_uses():
    from ingestion import watchlist_export as wx

    assert wx.plain_reason("noncurrent_loans_pct = 2.73 (peer median 0.46)") == (
        "Non-current loans: 2.73% of loans (median of similar-size banks: 0.46% of loans)"
    )
    assert wx.plain_reason("noncurrent_loans_pct = 2.73 (median 0.46)") == (
        "Non-current loans: 2.73% of loans (median of all US banks: 0.46% of loans)"
    )


def test_waterfall_and_distress_lines_for_the_website():
    import json as _json

    from ingestion import watchlist_export as wx

    raw = _json.dumps(
        {
            "base_pct": 4.1,
            "items": [{"f": "noncurrent_loans_pct", "v": 2.734, "m": 0.36, "pp": 12.345}],
            "other_pp": 1.2,
            "p_pct": 17.6,
        }
    )
    assert wx.waterfall(raw) == {
        "b": 4.1,
        "i": [{"f": "noncurrent_loans_pct", "l": "Non-current loans", "v": 2.73, "m": 0.36, "pp": 12.35}],
        "o": 1.2,
    }
    assert wx.waterfall(None) is None
    lines = wx.distress(_json.dumps([{"line": "tier1_leverage_below", "value": 4.1, "threshold": 5.0}]))
    assert lines == [{"k": "tier1_leverage_below", "l": "Tier 1 leverage ratio below 5%", "v": 4.1}]
    assert wx.distress(None) is None and wx.distress("[]") == []


def test_not_reported_waterfall_items_say_so():
    import json as _json

    from ingestion import watchlist_export as wx

    raw = _json.dumps(
        {
            "base_pct": 4.0,
            "items": [
                {"f": "brokered_to_deposits_pct", "v": None, "m": 0.0, "pp": 3.1},
                {"f": "ore_to_assets_pct", "v": None, "m": 0.0, "pp": -0.4},
            ],
            "other_pp": 0,
            "p_pct": 6.7,
        }
    )
    a, b = wx.waterfall(raw)["i"]
    assert a["nr"] == "Not reported: the missing figure itself raised the score" and a["v"] is None
    assert b["nr"] == "Not reported: the missing figure itself lowered the score"


# ------------------------------------------------------------------ waterfall files (schema 5)
# 6 Nov 2026: waterfalls inside watchlist.json made it 5.5 MB; they now live in one file per bank.
WF = json.dumps(
    {
        "base_pct": 4.0,
        "items": [{"f": "roa_pct", "v": -1.0, "m": 0.9, "pp": 30.0}],
        "other_pp": 1.0,
        "p_pct": 35.0,
    }
)


def test_banks_with_a_waterfall_are_flagged_and_carry_no_waterfall_inline():
    rows = [
        bank_row(cert=1, risk_band="High", distress_probability=0.6, shap_waterfall=WF),
        bank_row(cert=2, risk_band="Low", distress_probability=0.01),
    ]
    p = wx.build_payload(rows, [], peer_rows(), date(2026, 11, 6))
    wx.validate_payload(p)
    by_cert = {b["c"]: b for b in p["banks"]}
    assert by_cert[1]["wf"] == 1 and "w" not in by_cert[1]
    assert "wf" not in by_cert[2]
    assert p["meta"]["schema"] == 5 and p["meta"]["waterfallPath"] == "waterfalls/{c}.json"
    docs = wx.build_waterfalls(rows)
    assert set(docs) == {1}
    assert docs[1]["c"] == 1 and docs[1]["q"] == "2026-06-30" and docs[1]["w"]["i"][0]["f"] == "roa_pct"


def test_the_validator_refuses_a_waterfall_inside_watchlist_json():
    p = payload()
    p["banks"][0]["w"] = {"b": 4.0, "i": [], "o": 0.0}
    with pytest.raises(ValueError, match="waterfalls belong in waterfalls/"):
        wx.validate_payload(p)


def test_waterfall_files_replace_the_whole_folder(tmp_path):
    docs = wx.build_waterfalls([bank_row(cert=c, shap_waterfall=WF) for c in (1, 2)])
    assert wx.write_waterfalls(docs, tmp_path)[0] == 2
    assert sorted(p.name for p in (tmp_path / "waterfalls").iterdir()) == ["1.json", "2.json"]
    # next week bank 2 is gone and bank 3 is new: no stale file, no leftovers
    docs = wx.build_waterfalls([bank_row(cert=c, shap_waterfall=WF) for c in (1, 3)])
    n, total = wx.write_waterfalls(docs, tmp_path)
    assert n == 2 and total > 0
    assert sorted(p.name for p in (tmp_path / "waterfalls").iterdir()) == ["1.json", "3.json"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["waterfalls"]
    assert json.loads((tmp_path / "waterfalls" / "3.json").read_text())["c"] == 3


def test_a_bad_waterfall_file_leaves_last_weeks_folder_alone(tmp_path):
    good = wx.build_waterfalls([bank_row(cert=1, shap_waterfall=WF)])
    wx.write_waterfalls(good, tmp_path)
    bad = {5: {"c": 5, "q": "2026-06-30", "w": {"b": 1, "i": [{"f": "x"}] * 9, "o": 0}}}  # 9 items
    with pytest.raises(ValueError, match="at most 8 items"):
        wx.write_waterfalls(bad, tmp_path)
    assert sorted(p.name for p in (tmp_path / "waterfalls").iterdir()) == ["1.json"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["waterfalls"]  # no temporary folder left
