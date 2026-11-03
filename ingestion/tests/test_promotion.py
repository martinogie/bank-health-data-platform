"""The promotion gate (M-09), databricks/notebooks/_promotion.py, loaded directly. Offline."""

from datetime import date
from pathlib import Path

NOTEBOOK = Path(__file__).resolve().parents[2] / "databricks" / "notebooks" / "_promotion.py"
P: dict = {}
exec(compile(NOTEBOOK.read_text(encoding="utf-8"), str(NOTEBOOK), "exec"), P)

CHAMP = {"pr_auc": 0.333, "top_decile_capture": 0.526}  # v5 on 2020-2024
ROWS = P["rows_hash"]([(1, date(2020, 3, 31)), (2, date(2020, 3, 31))])


def gate(pr_auc, capture, rows=ROWS):
    return P["gate"]({"pr_auc": pr_auc, "top_decile_capture": capture}, CHAMP, rows, ROWS)


def test_row_hash_ignores_order_and_sees_any_change():
    a = P["rows_hash"]([(2, date(2020, 3, 31)), (1, date(2020, 3, 31))])
    assert a == ROWS
    assert P["rows_hash"]([(1, date(2020, 3, 31))]) != ROWS


def test_better_beyond_a_tolerance_passes():
    g = gate(0.345, 0.530)
    assert (g["decision"], g["promotable"]) == ("pass", True)
    assert gate(0.333, 0.540)["decision"] == "pass"  # capture +1.4 points


def test_within_both_tolerances_is_a_tie_and_promotable():
    g = gate(0.329, 0.517)  # -0.004 PR-AUC, -0.9 points
    assert (g["decision"], g["promotable"]) == ("tie", True)


def test_worse_beyond_a_tolerance_fails():
    g = gate(0.327, 0.530)  # PR-AUC -0.006
    assert (g["decision"], g["promotable"]) == ("fail", False)
    assert g["reasons"] == ["PR-AUC -0.0060 vs the champion (tolerance -0.005)"]
    g = gate(0.340, 0.514)  # capture -1.2 points, even though PR-AUC is better
    assert g["decision"] == "fail" and "top-10% capture -1.2 points" in g["reasons"][0]


def test_different_test_rows_fail_whatever_the_numbers():
    other = P["rows_hash"]([(1, date(2020, 3, 31))])
    g = gate(0.400, 0.600, rows=other)
    assert g["decision"] == "fail" and "not judged on the same test rows" in g["reasons"][0]


def test_no_champion_is_promotable():
    g = P["gate"](CHAMP, None, ROWS, None)
    assert (g["decision"], g["promotable"]) == ("no_champion", True)
