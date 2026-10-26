"""Unit tests for databricks/notebooks/_features.py: distress flags, labels and exclusions.

build_model_frame() runs on a real local Spark session against small hand-made bank histories,
so the tests exercise the same code 04_train_model and 05_score run on Databricks. The gold
and failures tables are served from memory (no files, no Databricks). Skipped when PySpark or
Java is not installed; CI installs both.

Timeline: 20 quarters, 2015 Q1 to 2019 Q4. Labels are complete up to 2018 Q4 (4 quarters
before the latest data).
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path

import pytest

pyspark = pytest.importorskip("pyspark")
if shutil.which("java") is None:
    pytest.skip("Java is not installed (needed by PySpark)", allow_module_level=True)

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import types as T  # noqa: E402

NOTEBOOK = Path(__file__).resolve().parents[2] / "databricks" / "notebooks" / "_features.py"


def quarter_end(year: int, q: int) -> dt.date:
    return {
        1: dt.date(year, 3, 31),
        2: dt.date(year, 6, 30),
        3: dt.date(year, 9, 30),
        4: dt.date(year, 12, 31),
    }[q]


QUARTERS = [quarter_end(y, q) for y in range(2015, 2020) for q in (1, 2, 3, 4)]
Q = {f"{d.year}Q{(d.month - 1) // 3 + 1}": d for d in QUARTERS}

DOUBLES = [
    "total_assets_k", "gross_loans_k", "total_deposits_k", "past_due_30_89_k", "past_due_90_plus_k",
    "nonaccrual_assets_k", "other_real_estate_owned_k", "net_income_qtr_k", "brokered_deposits_k",
    "construction_loans_k", "commercial_industrial_loans_k", "consumer_loans_k", "residential_re_loans_k",
    "agricultural_loans_k", "tier1_leverage_pct", "equity_to_assets_pct", "noncurrent_loans_pct",
    "net_chargeoff_rate_pct", "roa_pct", "roe_pct", "net_interest_margin_pct", "efficiency_ratio_pct",
    "loans_to_deposits_pct", "loan_growth_qoq_pct", "loan_growth_yoy_pct", "deposit_growth_yoy_pct",
    "asset_growth_yoy_pct", "cre_to_loans_pct", "cre_to_equity_pct", "log_total_assets", "bank_age_years",
]  # fmt: skip
GOLD_SCHEMA = T.StructType(
    [T.StructField("cert", T.IntegerType()), T.StructField("report_date", T.DateType())]
    + [
        T.StructField(c, T.StringType())
        for c in ("bank_name", "state_code", "asset_size_tier", "foreign_office_rule")
    ]
    + [
        T.StructField(c, T.BooleanType())
        for c in ("cblr_reporter", "is_foreign_office", "capital_zero_review")
    ]
    + [T.StructField(c, T.DoubleType()) for c in DOUBLES]
)
FAILURE_SCHEMA = T.StructType(
    [T.StructField("cert", T.IntegerType()), T.StructField("fail_date", T.DateType())]
)

HEALTHY = {
    "bank_name": "Test Bank", "state_code": "TX", "asset_size_tier": "2: $100M-$1B", "foreign_office_rule": None,
    "cblr_reporter": False, "is_foreign_office": False, "capital_zero_review": False,
    "total_assets_k": 500_000.0, "gross_loans_k": 300_000.0, "total_deposits_k": 400_000.0,
    "past_due_30_89_k": 3_000.0, "past_due_90_plus_k": 600.0, "nonaccrual_assets_k": 1_000.0,
    "other_real_estate_owned_k": 500.0, "net_income_qtr_k": 1_200.0, "brokered_deposits_k": 20_000.0,
    "construction_loans_k": 15_000.0, "commercial_industrial_loans_k": 60_000.0, "consumer_loans_k": 30_000.0,
    "residential_re_loans_k": 90_000.0, "agricultural_loans_k": 6_000.0, "tier1_leverage_pct": 10.0,
    "equity_to_assets_pct": 11.0, "noncurrent_loans_pct": 0.5, "net_chargeoff_rate_pct": 0.1, "roa_pct": 1.0,
    "roe_pct": 9.0, "net_interest_margin_pct": 3.5, "efficiency_ratio_pct": 60.0, "loans_to_deposits_pct": 75.0,
    "loan_growth_qoq_pct": 1.0, "loan_growth_yoy_pct": 4.0, "deposit_growth_yoy_pct": 3.0,
    "asset_growth_yoy_pct": 3.5, "cre_to_loans_pct": 30.0, "cre_to_equity_pct": 150.0, "log_total_assets": 13.1,
    "bank_age_years": 25.0,
}  # fmt: skip


def history(cert: int, quarters=QUARTERS, **overrides_by_quarter) -> list[dict]:
    """One row per quarter, healthy unless overridden: history(1, Q2017Q3={"noncurrent_loans_pct": 4})."""
    rows = []
    for d in quarters:
        row = {"cert": cert, "report_date": d, **HEALTHY}
        row.update(overrides_by_quarter.get(f"Q{d.year}Q{(d.month - 1) // 3 + 1}", {}))
        rows.append(row)
    return rows


def everywhere(cert: int, quarters=QUARTERS, **values) -> list[dict]:
    return [{**r, **values} for r in history(cert, quarters)]


BANKS = (
    history(1)  # healthy all along
    + history(2, Q2017Q3={"noncurrent_loans_pct": 4.0})  # NCL breach in one quarter
    + history(3, Q2017Q1={"net_income_qtr_k": -500.0}, Q2017Q2={"net_income_qtr_k": -800.0})  # 2 losses
    + history(4, Q2016Q2={"net_income_qtr_k": -500.0})  # a single loss is not distress
    + history(5, Q2018Q1={"tier1_leverage_pct": 4.0}, Q2016Q1={"tier1_leverage_pct": None})  # Tier 1; NULL
    + history(6, quarters=[d for d in QUARTERS if d <= Q["2017Q2"]])  # fails 2017-09-15
    + history(7, quarters=[d for d in QUARTERS if d <= Q["2016Q4"]])  # vanishes (merger), no failure
    + history(8, quarters=[d for d in QUARTERS if d != Q["2016Q2"]])  # one missing quarter
    + everywhere(9, is_foreign_office=True, foreign_office_rule="IBA")
    + everywhere(10, bank_age_years=2.0)  # new bank
    + everywhere(11, bank_age_years=None)  # unknown age: not treated as new
    + everywhere(12, total_assets_k=0.0, gross_loans_k=0.0)  # no assets: ratios undefined, not modelled
)
FAILURES = [{"cert": 6, "fail_date": dt.date(2017, 9, 15)}, {"cert": None, "fail_date": dt.date(2016, 1, 1)}]


class Tables:
    """Stands in for `spark` inside the notebook: spark.table(name) serves in-memory DataFrames."""

    def __init__(self, session, tables):
        self.session, self.tables = session, tables

    def table(self, name):
        return self.tables[name]


@pytest.fixture(scope="module")
def frame():
    # Spark starts Python workers itself; make them use this interpreter (on Windows it would
    # otherwise take the first `python` on PATH, which may not have pyspark).
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    session = (
        SparkSession.builder.master("local[1]")
        .appName("features-unit-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    tables = {
        "gold.bank_quarter": session.createDataFrame(BANKS, GOLD_SCHEMA),
        "silver.fdic_failures": session.createDataFrame(FAILURES, FAILURE_SCHEMA),
    }
    ns = {"spark": Tables(session, tables), "GOLD": "gold", "SILVER": "silver"}
    exec(compile(NOTEBOOK.read_text(encoding="utf-8"), str(NOTEBOOK), "exec"), ns)
    rows = ns["build_model_frame"]().collect()
    yield {(r["cert"], r["report_date"]): r.asDict() for r in rows}, ns
    session.stop()


def at(frame, cert, quarter):
    return frame[0][(cert, Q[quarter])]


# ---------------------------------------------------------------- shape and features
def test_one_row_per_bank_quarter_and_every_feature_present(frame):
    rows, ns = frame
    assert len(rows) == len(BANKS)
    assert set(ns["FEATURES"]) <= set(next(iter(rows.values())))


def test_risk_based_capital_stays_out_of_the_features(frame):
    _, ns = frame
    assert not set(ns["RISK_BASED_CAPITAL_FEATURES"]) & set(ns["FEATURES"])


def test_ratios_are_computed_and_undefined_without_a_denominator(frame):
    r = at(frame, 1, "2017Q1")
    assert r["past_due_30_89_to_loans_pct"] == pytest.approx(1.0)  # 3,000 / 300,000
    assert r["brokered_to_deposits_pct"] == pytest.approx(5.0)
    assert r["net_income_qtr_to_assets_pct"] == pytest.approx(0.24)
    z = at(frame, 12, "2017Q1")
    assert z["past_due_30_89_to_loans_pct"] is None and z["ore_to_assets_pct"] is None


def test_changes_use_the_exact_prior_quarter(frame):
    r = at(frame, 2, "2017Q4")  # NCL 0.5 after 4.0 in 2017 Q3
    assert r["noncurrent_change_1q_pp"] == pytest.approx(-3.5)
    assert at(frame, 2, "2018Q3")["noncurrent_change_4q_pp"] == pytest.approx(-3.5)
    # bank 8 has no 2016 Q2: comparisons with that quarter are NULL, not with another quarter
    assert at(frame, 8, "2016Q3")["noncurrent_change_1q_pp"] is None
    assert at(frame, 8, "2017Q2")["noncurrent_change_4q_pp"] is None
    assert at(frame, 8, "2017Q3")["noncurrent_change_4q_pp"] == pytest.approx(0.0)


# ---------------------------------------------------------------- distress at a quarter
def test_distress_conditions(frame):
    assert at(frame, 2, "2017Q3")["is_distressed"]  # non-current loans > 3%
    assert at(frame, 5, "2018Q1")["is_distressed"]  # Tier 1 leverage < 5%
    assert not at(frame, 5, "2016Q1")["is_distressed"]  # a NULL ratio is not a breach
    assert not at(frame, 3, "2017Q1")["is_distressed"]  # first loss quarter
    assert at(frame, 3, "2017Q2")["is_distressed"]  # second loss in a row
    assert not any(at(frame, 4, q)["is_distressed"] for q in ("2016Q2", "2016Q3"))  # one loss only
    assert not any(r["is_distressed"] for (c, _), r in frame[0].items() if c == 1)


# ---------------------------------------------------------------- labels
def test_label_looks_at_the_next_four_quarters_only(frame):
    labels = {q: at(frame, 2, q)["distress_next_4q"] for q in ("2016Q2", "2016Q3", "2017Q2", "2017Q3")}
    assert labels == {"2016Q2": 0, "2016Q3": 1, "2017Q2": 1, "2017Q3": 0}  # breach in 2017 Q3


def test_challenger_label_ignores_the_two_losses_rule(frame):
    r = at(frame, 3, "2016Q3")  # two losses in 2017 Q1-Q2 are within t+1..t+4
    assert r["distress_next_4q"] == 1
    assert r["distress_strict_next_4q"] == 0
    assert at(frame, 2, "2016Q3")["distress_strict_next_4q"] == 1  # NCL breach counts for both


def test_failure_within_twelve_months_sets_the_label(frame):
    inside = at(frame, 6, "2016Q3")  # 2016-09-30 -> fails 2017-09-15, inside 12 months
    assert inside["fails_within_12m"] == 1 and inside["distress_next_4q"] == 1
    assert inside["distress_strict_next_4q"] == 1
    outside = at(frame, 6, "2016Q2")  # 2016-06-30 -> window ends 2017-06-30, before the failure
    assert outside["fails_within_12m"] == 0 and outside["distress_next_4q"] == 0


# ---------------------------------------------------------------- which rows are modelled
def test_healthy_complete_rows_are_modelled(frame):
    assert at(frame, 1, "2017Q1")["is_model_row"]
    assert at(frame, 1, "2018Q4")["is_model_row"]  # last quarter with a complete label


def test_last_four_quarters_have_incomplete_labels(frame):
    for q in ("2019Q1", "2019Q2", "2019Q3", "2019Q4"):
        r = at(frame, 1, q)
        assert not r["label_complete"] and not r["is_model_row"], q


def test_already_distressed_quarters_are_not_modelled(frame):
    assert not at(frame, 2, "2017Q3")["is_model_row"]
    assert at(frame, 2, "2017Q2")["is_model_row"]


def test_outcome_must_be_observed(frame):
    gone = at(frame, 7, "2016Q1")  # vanishes after 2016 Q4 without failing: outcome unknown
    assert not gone["outcome_observed"] and not gone["is_model_row"]
    assert at(frame, 7, "2015Q4")["outcome_observed"]  # 2016 Q4 is exactly t+4
    failed = at(frame, 6, "2016Q3")  # stops reporting, but the failure is the outcome
    assert failed["outcome_observed"] and failed["is_model_row"]


def test_foreign_offices_new_banks_and_empty_banks_are_not_modelled(frame):
    assert not at(frame, 9, "2017Q1")["is_model_row"]
    new = at(frame, 10, "2017Q1")
    assert new["is_new_bank"] and not new["is_model_row"]
    unknown_age = at(frame, 11, "2017Q1")
    assert not unknown_age["is_new_bank"] and unknown_age["is_model_row"]
    assert not at(frame, 12, "2017Q1")["is_model_row"]  # total assets 0
