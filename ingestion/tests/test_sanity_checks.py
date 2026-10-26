import pytest

from ingestion import sanity_checks as sc


def row(cert=1, **over):
    r = {
        "cert": cert,
        "bank_name": f"Bank {cert}",
        "state_code": "TX",
        "bank_age_years": 25.0,
        "distress_probability": 0.6,
        "risk_band": "High",
        "is_new_bank": False,
        "is_foreign_office": False,
        "tier1_leverage_pct": 7.5,
        "reason_1": "noncurrent_loans_pct = 4.20 (median 0.40)",
        "reason_2": "tier1_leverage_pct = 4.80 (median 10.90)",
        "reason_3": None,
    }
    r.update(over)
    return r


def test_clean_top20_passes():
    assert sc.problems_in_top([row(i) for i in range(20)]) == []


@pytest.mark.parametrize(
    "over, expected",
    [
        ({"is_new_bank": True, "bank_age_years": 1.2}, "new bank"),
        ({"bank_age_years": 2.9}, "new bank"),
        ({"is_foreign_office": True}, "foreign bank"),
        ({"tier1_leverage_pct": 0.0}, "Tier 1 leverage is 0%"),
        ({"tier1_leverage_pct": None}, "not reported"),
        ({"reason_2": "tier1_leverage_pct = 0.00 (median 10.90)"}, "reason_2"),
        ({"reason_3": "total_risk_based_capital_pct = 15.00 (median 15.00)"}, "reason_3"),
    ],
)
def test_each_rule_fails(over, expected):
    problems = sc.problems_in_top([row(1, **over)] + [row(i) for i in range(2, 21)])
    assert len(problems) == 1 and expected in problems[0] and "CERT 1" in problems[0]


def test_negative_tier1_is_allowed():
    # real negative capital at an insolvent bank is a genuine signal, not a reporting artefact
    assert (
        sc.problems_in_top(
            [row(1, tier1_leverage_pct=-2.1, reason_2="tier1_leverage_pct = -2.10 (median 10.9)")]
        )
        == []
    )


def test_sql_selects_scored_bands_only():
    sql = sc.top_sql("RAW.ML_RISK_SCORES")
    assert "risk_band in ('High', 'Elevated', 'Watch', 'Low')" in sql
    assert sql.endswith("limit 20")


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.description = [(c,) for c in sc.COLUMNS]

    def execute(self, sql):
        self.sql = sql

    def fetchall(self):
        return [tuple(r[c] for c in sc.COLUMNS) for r in self.rows]


def test_run_raises_with_every_problem_listed(capsys):
    rows = [row(1, is_foreign_office=True), row(2, tier1_leverage_pct=0.0)] + [row(i) for i in range(3, 21)]
    with pytest.raises(ValueError, match="2 problems"):
        sc.run(FakeCursor(rows), "RAW.ML_RISK_SCORES")
    assert "Bank 1" in capsys.readouterr().out  # the table is printed before failing


def test_run_needs_twenty_banks():
    with pytest.raises(ValueError, match="only 3"):
        sc.run(FakeCursor([row(i) for i in range(3)]), "T")
