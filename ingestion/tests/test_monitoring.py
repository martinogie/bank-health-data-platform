"""Model monitoring (M-08): PSI, capture, backtest and alert rules (databricks/notebooks/_monitoring.py,
loaded directly so the tests run the same code), and ingestion/model_monitoring.py. Offline."""

from pathlib import Path

import numpy as np

from ingestion import model_monitoring as mm

NOTEBOOK = Path(__file__).resolve().parents[2] / "databricks" / "notebooks" / "_monitoring.py"
M: dict = {}
exec(compile(NOTEBOOK.read_text(encoding="utf-8"), str(NOTEBOOK), "exec"), M)
RNG = np.random.default_rng(20261103)


# ------------------------------------------------------------------ PSI
def test_same_distribution_is_stable_and_a_shift_is_large():
    ref = RNG.normal(0, 1, 20_000)
    assert M["psi"](ref, RNG.normal(0, 1, 5_000)) < 0.01
    shifted = M["psi"](ref, RNG.normal(1.0, 1, 5_000))
    assert shifted > M["PSI_LARGE"]
    assert (
        M["psi_level"](shifted) == "large"
        and M["psi_level"](0.15) == "moderate"
        and M["psi_level"](0.01) == "stable"
    )


def test_missing_values_are_their_own_bin():
    ref = np.r_[RNG.normal(0, 1, 9_000), [np.nan] * 1_000]  # 10% missing
    same = np.r_[RNG.normal(0, 1, 900), [np.nan] * 100]
    more_missing = np.r_[RNG.normal(0, 1, 500), [np.nan] * 500]  # 50% missing
    assert M["psi"](ref, same) < 0.02
    assert M["psi"](ref, more_missing) > M["PSI_LARGE"]


def test_values_beyond_the_reference_range_still_count():
    ref = np.linspace(0, 1, 1_000)
    assert M["psi"](ref, np.full(100, 5.0)) > M["PSI_LARGE"]  # all above the reference maximum


def test_empty_sides_give_no_psi():
    assert M["psi"]([], [1.0]) is None and M["psi"]([1.0], []) is None and M["psi_level"](None) is None


# ------------------------------------------------------------------ backtest
def test_capture_matches_the_training_definition():
    y = np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    p = np.arange(20)[::-1] / 20  # row 0 riskiest; top ceil(10%) = 2 rows: rows 0 and 1
    assert M["capture"](y, p) == 0.5


def test_a_quarter_needs_30_positives_to_be_judged():
    y = np.r_[np.ones(29), np.zeros(971)]
    row = M["backtest_row"]("2025-03-31", y, RNG.random(1_000), lambda a, b: 0.3)
    assert (row["positives"], row["judged"], row["pr_auc"]) == (29, False, 0.3)
    assert M["backtest_row"](
        "2025-03-31", np.r_[np.ones(30), np.zeros(970)], RNG.random(1_000), lambda a, b: 0.3
    )["judged"]


# ------------------------------------------------------------------ alert rules
def psi_row(name, value, reference="previous_4q"):
    return {"reference": reference, "name": name, "psi": value, "level": M["psi_level"](value)}


def bt(date, capture, judged=True):
    return {
        "report_date": date,
        "rows": 4000,
        "positives": 250,
        "judged": judged,
        "pr_auc": 0.35,
        "top10_capture": capture,
    }


def test_the_real_dev_numbers_do_not_alert():
    psi_rows = [
        psi_row("score", 0.008),
        psi_row("roa_pct", 0.034),
        psi_row("score", 0.323, "training_2001_2019"),
    ]
    psi_rows += [
        psi_row(f"f{i}", 0.6, "training_2001_2019") for i in range(12)
    ]  # large vs training: reported only
    assert M["alerts"](psi_rows, [bt("2022-09-30", 0.451), bt("2025-06-30", 0.559)]) == []


def test_a_large_score_shift_alerts():
    (a,) = M["alerts"]([psi_row("score", 0.31)], [])
    assert a.startswith("score PSI 0.310 vs the previous 4 quarters")


def test_three_features_with_a_large_shift_alert_two_do_not():
    two = [psi_row("score", 0.01), psi_row("a", 0.4), psi_row("b", 0.3)]
    assert M["alerts"](two, []) == []
    (a,) = M["alerts"](two + [psi_row("c", 0.26)], [])
    assert a.startswith("3 features with PSI above 0.25") and "a 0.40" in a


def test_a_sharp_capture_drop_alerts_only_when_judged():
    assert M["alerts"]([], [bt("2026-03-31", 0.42, judged=False)]) == []
    (a,) = M["alerts"]([], [bt("2026-03-31", 0.42)])
    assert a == "2026-03-31: top-10% capture 42.0%, more than 10 points below the test result 52.6%"
    assert M["alerts"]([], [bt("2026-03-31", 0.427)]) == []  # 42.7%: within the 10 points


# ------------------------------------------------------------------ Airflow side
SUMMARY = {
    "latest_quarter": "2026-06-30",
    "model": "bank_health.ml.distress_model v5",
    "psi": [
        psi_row("score", 0.0079),
        psi_row("score", 0.3226, "training_2001_2019"),
        psi_row("roa_pct", 0.0342),
    ],
    "backtest": [bt("2025-03-31", 0.4912), bt("2025-06-30", 0.5593)],
    "alerts": [],
}


def test_rows_one_per_psi_value_and_backtest_metric():
    rows = mm.rows(SUMMARY, "run-1", "dev")
    assert len(rows) == 3 + 2 * 2
    assert all(set(r) == set(mm.COLUMNS) for r in rows)
    capture = [r for r in rows if r["NAME"] == "top10_capture"]
    assert [(r["REPORT_DATE"], r["VALUE"], r["JUDGED"]) for r in capture] == [
        ("2025-03-31", 0.4912, True),
        ("2025-06-30", 0.5593, True),
    ]


def test_headline():
    assert mm.headline(SUMMARY) == (
        "Model monitoring: score PSI 0.008 vs previous 4 quarters, 0.323 vs 2001-2019, "
        "0 feature(s) with a large shift, backtest 2025-06-30: top-10% capture 55.9%"
    )


def test_alerts_are_off_unless_switched_on():
    assert not mm.alerts_enabled(None) and not mm.alerts_enabled("off")
    assert mm.alerts_enabled("on") and mm.alerts_enabled("TRUE")


class Cur:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(("execute", sql, params))

    def executemany(self, sql, seq):
        self.calls.append(("executemany", sql, list(seq)))


def test_write_replaces_this_runs_rows():
    cur = Cur()
    assert mm.write(cur, "run-1", mm.rows(SUMMARY, "run-1", "dev")) == 7
    (kind1, sql1, params1), (kind2, sql2, rows2) = cur.calls
    assert sql1.startswith("delete from OPS.MODEL_MONITORING") and params1 == {"r": "run-1"}
    assert kind2 == "executemany" and sql2.startswith("insert into OPS.MODEL_MONITORING") and len(rows2) == 7
