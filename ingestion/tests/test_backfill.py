"""Backfill (redo a past period): the date rules, the script, and what a backfill run changes.
Offline: no Airflow, no network (fake HTTP session, fake subprocess runner)."""

import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ingestion import backfill, cfpb, run_log, volume_checks
from ingestion.tests.conftest import FakeResponse, FakeSession

CHICAGO = ZoneInfo("America/Chicago")
TUESDAY = date(2026, 11, 3)
FDIC_WM = date(2026, 6, 30)
CFPB_WM = date(2026, 11, 1)


def plan(prod=False, today=TUESDAY, fdic_wm=FDIC_WM, cfpb_wm=CFPB_WM, **params):
    return backfill.plan(params, today, fdic_wm, cfpb_wm, is_prod=prod)


def refused(**kw) -> str:
    with pytest.raises(backfill.BackfillRefused) as exc:
        plan(**kw)
    return str(exc.value)


# ------------------------------------------------------------------ the date rules
def test_no_dates_is_the_normal_weekly_run():
    assert plan() == {"mode": "weekly"}
    assert plan(backfill_fdic_from=None, backfill_cfpb_to="", retrain_model=True) == {"mode": "weekly"}


def test_an_fdic_range_redoes_the_quarters_ending_in_it():
    p = plan(backfill_fdic_from="2026-01-01", backfill_fdic_to="2026-06-30")
    assert p["mode"] == "backfill"
    assert p["fdic_quarters"] == ["2026-03-31", "2026-06-30"]
    assert p["cfpb_from"] is None
    assert (p["fdic_watermark"], p["cfpb_watermark"]) == ("2026-06-30", "2026-11-01")  # unchanged


def test_a_cfpb_range_and_both_together():
    p = plan(backfill_cfpb_from="2026-09-01", backfill_cfpb_to="2026-09-30")
    assert (p["fdic_quarters"], p["cfpb_from"], p["cfpb_to"]) == ([], "2026-09-01", "2026-09-30")
    p = plan(
        backfill_fdic_from="2026-06-30", backfill_fdic_to="2026-06-30",
        backfill_cfpb_from="2026-09-01", backfill_cfpb_to="2026-09-30",
    )  # fmt: skip
    assert backfill.describe(p) == (
        "Backfill: FDIC 2026-06-30..2026-06-30 (1 quarter), CFPB 2026-09-01..2026-09-30; "
        "watermarks not moved, no retrain"
    )


def test_from_must_be_before_to_and_both_are_needed():
    assert "from 2026-09-30 is after to 2026-09-01" in refused(
        backfill_cfpb_from="2026-09-30", backfill_cfpb_to="2026-09-01"
    )
    assert "FDIC quarters: give both a from and a to date" in refused(backfill_fdic_from="2026-03-31")


def test_no_future_dates():
    msg = refused(backfill_cfpb_from="2026-11-01", backfill_cfpb_to="2026-11-20")
    assert "to 2026-11-20 is in the future (today is 2026-11-03)" in msg


def test_not_newer_than_what_the_weekly_run_has_loaded():
    assert "2026-09-30 is newer than the newest quarter loaded (2026-06-30)" in refused(
        backfill_fdic_from="2026-06-30", backfill_fdic_to="2026-10-01"
    )
    assert "to 2026-11-02 is after the last day loaded (2026-11-01)" in refused(
        backfill_cfpb_from="2026-10-01", backfill_cfpb_to="2026-11-02"
    )


def test_not_before_the_history_kept():
    assert "before the history start 2000-03-31" in refused(
        backfill_fdic_from="1999-01-01", backfill_fdic_to="2000-06-30"
    )
    assert "before 2023-11-01 (the pipeline keeps 3 years)" in refused(
        backfill_cfpb_from="2023-10-01", backfill_cfpb_to="2023-12-31"
    )


def test_a_range_with_no_quarter_end_is_refused():
    msg = refused(backfill_fdic_from="2026-04-01", backfill_fdic_to="2026-06-15")
    assert "no quarter-end (Mar 31, Jun 30, Sep 30, Dec 31) between 2026-04-01 and 2026-06-15" in msg


def test_no_watermark_yet_is_refused():
    assert "no FDIC watermark yet" in refused(
        fdic_wm=None, backfill_fdic_from="2026-03-31", backfill_fdic_to="2026-03-31"
    )
    assert "no CFPB watermark yet" in refused(
        cfpb_wm=None, backfill_cfpb_from="2026-09-01", backfill_cfpb_to="2026-09-30"
    )


def test_a_backfill_never_retrains():
    for value in (True, "true", "True"):
        msg = refused(retrain_model=value, backfill_cfpb_from="2026-09-01", backfill_cfpb_to="2026-09-30")
        assert "a backfill never retrains" in msg
    assert plan(retrain_model="false", backfill_cfpb_from="2026-09-01", backfill_cfpb_to="2026-09-30")


def test_no_prod_backfills_on_a_monday_but_dev_may():
    monday = date(2026, 11, 2)
    params = {"backfill_cfpb_from": "2026-09-01", "backfill_cfpb_to": "2026-09-30"}
    assert "no backfills on a Monday" in refused(prod=True, today=monday, **params)
    assert plan(prod=False, today=monday, **params)["mode"] == "backfill"
    assert plan(prod=True, today=TUESDAY, **params)["mode"] == "backfill"


def test_every_problem_is_listed_at_once():
    msg = refused(
        prod=True, today=date(2026, 11, 2), retrain_model=True,
        backfill_fdic_from="2026-06-30", backfill_fdic_to="2026-03-31",
        backfill_cfpb_from="2026-09-01", backfill_cfpb_to="2026-12-01",
    )  # fmt: skip
    assert msg.startswith("Backfill refused: ")
    for part in (
        "after to 2026-03-31",
        "in the future",
        "never retrains",
        "Monday",
        "after the last day loaded",
    ):
        assert part in msg


def test_a_date_that_is_not_a_date_is_refused():
    with pytest.raises(backfill.BackfillRefused, match="not a date"):
        plan(backfill_cfpb_from="Sept 1", backfill_cfpb_to="2026-09-30")


# ------------------------------------------------------------------ scripts/backfill.py
def load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "backfill.py"
    spec = importlib.util.spec_from_file_location("backfill_script", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["backfill_script"] = module
    spec.loader.exec_module(module)
    return module


class FakeRun:
    def __init__(self, returncode=0):
        self.calls, self.returncode = [], returncode

    def __call__(self, cmd, cwd=None):
        self.calls.append((cmd, cwd))
        return type("R", (), {"returncode": self.returncode})()


def test_script_triggers_the_weekly_dag_with_the_dates():
    script, run = load_script(), FakeRun()
    code = script.main(["--fdic", "2026-03-31:2026-06-30", "--cfpb", "2026-09-01:2026-09-30"], TUESDAY, run)
    assert code == 0
    ((cmd, cwd),) = run.calls
    assert cmd[:9] == [
        "docker", "compose", "exec", "-T", "airflow-scheduler", "airflow", "dags", "trigger", "bank_health_weekly",
    ]  # fmt: skip
    assert json.loads(cmd[-1]) == {
        "backfill_fdic_from": "2026-03-31", "backfill_fdic_to": "2026-06-30",
        "backfill_cfpb_from": "2026-09-01", "backfill_cfpb_to": "2026-09-30",
    }  # fmt: skip
    assert cwd.name == "airflow"


def test_script_one_date_means_one_day_or_quarter():
    script, run = load_script(), FakeRun()
    assert script.main(["--fdic", "2026-06-30"], TUESDAY, run) == 0
    assert json.loads(run.calls[0][0][-1]) == {
        "backfill_fdic_from": "2026-06-30",
        "backfill_fdic_to": "2026-06-30",
    }


def test_script_dry_run_and_bad_dates_trigger_nothing(capsys):
    script, run = load_script(), FakeRun()
    assert script.main(["--cfpb", "2026-09-01:2026-09-30", "--dry-run"], TUESDAY, run) == 0
    assert script.main(["--cfpb", "2026-09-30:2026-09-01"], TUESDAY, run) == 2
    assert script.main(["--cfpb", "2026-11-01:2026-12-01"], TUESDAY, run) == 2
    assert run.calls == []
    assert "is in the future" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        script.main([], TUESDAY, run)  # neither --fdic nor --cfpb
    with pytest.raises(SystemExit):
        script.main(["--cfpb", "2026-09-xx"], TUESDAY, run)


# ------------------------------------------------------------------ CFPB re-download
def test_cfpb_backfill_downloads_again_even_when_the_month_is_on_disk(tmp_path):
    old = cfpb.window_path(tmp_path, date(2026, 8, 1), date(2026, 8, 31))
    old.parent.mkdir(parents=True)
    old.write_bytes(b"cached")
    body = b"Date received,Product,Company,Complaint ID\n2026-08-01,Mortgage,BANK,1\n"

    def handler(method, url, kw):
        if dict(kw["params"]).get("format") == "csv":
            return FakeResponse(content=body)
        return FakeResponse(payload={"hits": {"total": {"value": 1}}})

    result = cfpb.download_range(date(2026, 8, 1), date(2026, 9, 15), tmp_path, FakeSession(handler))
    assert result["cached"] == []
    assert [p.name for p in result["fresh"]] == [
        "complaints_2026-08-01_2026-08-31.csv.gz",  # same name as the weekly run's file: overwritten
        "complaints_2026-09-01_2026-09-15.csv.gz",
    ]
    assert old.read_bytes() != b"cached"


# ------------------------------------------------------------------ volume checks and run log
def _history(n=4):
    detail = {"bronze_added": {"cfpb_complaints": 50_000, "fdic_financials": 8_666,
                               "fdic_institutions": 27_835, "fdic_failures": 4_117}}  # fmt: skip
    return [
        {"SILVER_ROWS": 2_700_000, "GOLD_ROWS": 743_802, "MARTS_ROWS": 2_800_000, "SCORED_BANKS": 4257,
         "ROWS_DETAIL": detail, "LATEST_FDIC_QUARTER": "2026-06-30"}
        for _ in range(n)
    ]  # fmt: skip


def test_in_a_backfill_rows_added_are_reported_not_failed():
    current = {"SILVER_ROWS": 2_700_000, "GOLD_ROWS": 743_802, "MARTS_ROWS": 2_800_000, "SCORED_BANKS": 4257}
    added = {"cfpb_complaints": 900_000, "fdic_financials": 0, "fdic_institutions": 0, "fdic_failures": 0}
    weekly = volume_checks.compare(current, _history(), current_added=added)
    assert len(weekly["anomalies"]) == 4 and weekly["reported"] == []
    out = volume_checks.compare(current, _history(), current_added=added, backfill=True)
    assert out["anomalies"] == []
    assert {a["metric"] for a in out["reported"]} == {
        f"BRONZE_ADDED.{s}" for s in volume_checks.BRONZE_SOURCES
    }
    assert "backfill: rows added are reported, not checked" in out["note"]


def test_in_a_backfill_every_other_check_still_applies():
    current = {"SILVER_ROWS": 2_600_000, "GOLD_ROWS": 743_802, "MARTS_ROWS": 2_800_000, "SCORED_BANKS": 4257}
    out = volume_checks.compare(
        current, _history(), current_added={"cfpb_complaints": 900_000}, backfill=True
    )
    assert [a["metric"] for a in out["anomalies"]] == ["SILVER_ROWS"]  # silver dropped 3.7%


def test_backfill_runs_are_left_out_of_the_volume_history():
    class Cur:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return []

    cur = Cur()
    volume_checks.fetch_history(cur, "this-run")
    assert "ROWS_DETAIL:backfill is null" in cur.sql and "STATE = 'success'" in cur.sql


def _row(backfill_plan):
    return run_log.build_row(
        run_id="manual__2026-11-03", environment="prod", host_id="oracle-vm", dag_id="bank_health_weekly",
        started_at=datetime(2026, 11, 3, 9, tzinfo=CHICAGO),
        ended_at=datetime(2026, 11, 3, 9, 30, tzinfo=CHICAGO),
        state="success", failed_tasks=[], scheduled=False, backfill=backfill_plan,
    )  # fmt: skip


def test_the_run_log_marks_a_backfill_and_the_message_says_so():
    p = plan(backfill_cfpb_from="2026-09-01", backfill_cfpb_to="2026-09-30")
    row = _row(p)
    assert row["ROWS_DETAIL"]["backfill"]["cfpb_from"] == "2026-09-01"
    assert "mode" not in row["ROWS_DETAIL"]["backfill"]
    title, lines = run_log.success_message(row)
    assert title == "OK: bank_health_weekly backfill succeeded"
    assert lines[1] == "Backfill: CFPB 2026-09-01..2026-09-30; watermarks not moved, no retrain"
    assert lines[-1].endswith("n/a (manual run)")


def test_a_weekly_run_is_not_marked():
    row = _row({"mode": "weekly"})
    assert row["ROWS_DETAIL"] is None
    assert run_log.success_message(row)[0] == "OK: bank_health_weekly succeeded"
