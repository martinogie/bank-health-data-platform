"""The complaint match rate: kept in every run's log, alert on a sharp drop (offline)."""

from ingestion import run_log, volume_checks


def run(rate, companies=142):
    return {
        "ROWS_DETAIL": {"match": {"complaints_matched_to_bank_pct": rate, "companies_matched": companies}}
    }


HISTORY = [run(30.0), run(30.1), run(29.9), run(30.0)]


def test_the_run_log_keeps_the_match_rate():
    outputs = {
        "silver": {
            "rows": {"cfpb_complaints": 10},
            "complaints_matched_to_bank_pct": 30.0,
            "companies_matched": 142,
        }
    }
    assert run_log.layer_rows(outputs)["detail"]["match"] == {
        "complaints_matched_to_bank_pct": 30.0,
        "companies_matched": 142,
    }
    assert "match" not in run_log.layer_rows({"silver": {"rows": {}}})["detail"]


def test_a_normal_week_passes_and_a_small_dip_too():
    assert volume_checks.check_match_rate(run(30.1), HISTORY) == []
    assert volume_checks.check_match_rate(run(28.1), HISTORY) == []  # 1.9 points down: within the limit
    assert volume_checks.usual_match_rate(HISTORY) == 30.0


def test_a_sharp_drop_alerts():
    (a,) = volume_checks.check_match_rate(run(12.4), HISTORY)
    assert a["metric"] == "MATCH_RATE" and a["drop_pts"] == 17.6
    assert volume_checks.describe(a) == (
        "complaints matched to a bank: 12.4% vs usual 30.0% (down 17.6 points; limit 2.0)"
    )


def test_no_alert_until_three_earlier_runs_have_a_rate():
    assert volume_checks.check_match_rate(run(5.0), [run(30.0), run(30.0), {"ROWS_DETAIL": {}}]) == []


def test_compare_includes_it_also_in_a_backfill():
    current = {"SILVER_ROWS": 1, **run(10.0)}
    for backfill in (False, True):
        out = volume_checks.compare(current, HISTORY, current_added={}, backfill=backfill)
        assert [a["metric"] for a in out["anomalies"]] == ["MATCH_RATE"]
        assert out["usual_match_rate"] == 30.0


def test_the_success_message_shows_the_rate():
    row = {"RUN_ID": "r", "DAG_ID": "bank_health_weekly", "ROWS_DETAIL": run(30.0)["ROWS_DETAIL"]}
    _, lines = run_log.success_message(row)
    assert "Complaints matched to a bank: 30.0% (142 companies)" in lines
