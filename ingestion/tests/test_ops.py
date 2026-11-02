"""Unit tests for the Phase 2 operations modules: scheduler guard, SLO, freshness, volume
checks and the run log. Snowflake and Databricks are fakes; everything runs offline."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from ingestion import freshness, run_log, scheduler_guard, slo, volume_checks


class FakeCursor:
    """Answers queries by the first matching substring in `answers`; records every query."""

    def __init__(self, answers: dict[str, list] | None = None):
        self.answers = answers or {}
        self.queries: list[tuple[str, dict | None]] = []
        self._rows: list = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        for key, rows in self.answers.items():
            if key in sql:
                if isinstance(rows, Exception):
                    raise rows
                self._rows = list(rows)
                return
        self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def chicago(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=slo.TZ)


# ------------------------------------------------------------------ scheduler guard
def test_guard_lets_the_assigned_machine_run():
    cur = FakeCursor({"PROD_SCHEDULER": [("oracle-vm", "2026-11-05", "MARTINS")]})
    assert scheduler_guard.check(cur, "oracle-vm") == "oracle-vm"


def test_guard_stops_any_other_machine():
    cur = FakeCursor({"PROD_SCHEDULER": [("oracle-vm", "2026-11-05", "MARTINS")]})
    with pytest.raises(scheduler_guard.SchedulerNotAuthorized, match="This machine is 'pc'.*'oracle-vm'"):
        scheduler_guard.check(cur, "pc")


@pytest.mark.parametrize("host", [None, "", "  "])
def test_guard_needs_a_host_id(host):
    with pytest.raises(scheduler_guard.SchedulerNotAuthorized, match="PROD_HOST_ID is not set"):
        scheduler_guard.check(FakeCursor(), host)


def test_guard_stops_when_it_cannot_read_its_table():
    cur = FakeCursor({"PROD_SCHEDULER": RuntimeError("Object does not exist or not authorized")})
    with pytest.raises(
        scheduler_guard.SchedulerNotAuthorized, match="Cannot read .*Not running.*setup_ops.sql"
    ):
        scheduler_guard.check(cur, "pc")


@pytest.mark.parametrize("rows", [[], [("pc", None, None), ("oracle-vm", None, None)]])
def test_guard_needs_exactly_one_row(rows):
    with pytest.raises(scheduler_guard.SchedulerNotAuthorized, match="exactly one row"):
        scheduler_guard.check(FakeCursor({"PROD_SCHEDULER": rows}), "pc")


# ------------------------------------------------------------------ SLO
def test_deadline_is_monday_noon_chicago_in_summer_and_winter():
    summer = slo.deadline(chicago(2026, 10, 28, 9))  # a Wednesday, CDT
    winter = slo.deadline(chicago(2026, 11, 4, 9))  # after clocks go back, CST
    assert summer == chicago(2026, 10, 26, 12) and summer.utcoffset() == timedelta(hours=-5)
    assert winter == chicago(2026, 11, 2, 12) and winter.utcoffset() == timedelta(hours=-6)


def test_slo_met_only_for_success_before_the_deadline():
    start = chicago(2026, 11, 2, 6)
    assert slo.slo_met("success", start, chicago(2026, 11, 2, 6, 25)) is True
    assert slo.slo_met("success", start, chicago(2026, 11, 2, 12, 1)) is False
    assert slo.slo_met("failed", start, chicago(2026, 11, 2, 6, 25)) is False
    assert slo.slo_met("success", None, None) is None


def test_week_check_before_the_deadline_is_always_ok():
    ok, msg = slo.check_week(FakeCursor(), chicago(2026, 11, 2, 11, 59))
    assert ok and "before the deadline" in msg


def test_week_check_passes_with_an_on_time_success():
    rows = [("scheduled__x", "success", chicago(2026, 11, 2, 6), chicago(2026, 11, 2, 6, 30))]
    ok, msg = slo.check_week(FakeCursor({"PIPELINE_RUNS": rows}), chicago(2026, 11, 2, 12))
    assert ok and "scheduled__x succeeded" in msg


@pytest.mark.parametrize(
    ("rows", "expect"),
    [
        ([], "no run has started this week"),
        ([("r1", "failed", chicago(2026, 11, 2, 6), chicago(2026, 11, 2, 6, 20))], "r1: failed"),
        (
            [("r2", "success", chicago(2026, 11, 2, 9), chicago(2026, 11, 2, 12, 30))],
            "r2: success, ended Mon 12:30",
        ),
        ([("r3", "running", chicago(2026, 11, 2, 9), None)], "r3: running, not finished"),
    ],
)
def test_week_check_alerts_when_late_or_failed(rows, expect):
    ok, msg = slo.check_week(FakeCursor({"PIPELINE_RUNS": rows}), chicago(2026, 11, 2, 12, 5))
    assert not ok and expect in msg and "no successful run by Mon 02 Nov 12:00 CST" in msg


# ------------------------------------------------------------------ freshness
@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 27), date(2026, 6, 30)),
        (date(2026, 9, 12), date(2026, 3, 31)),
        (date(2026, 9, 13), date(2026, 6, 30)),
        (date(2026, 12, 14), date(2026, 9, 30)),
        (date(2027, 3, 16), date(2026, 12, 31)),
    ],
)
def test_expected_fdic_quarter(today, expected):
    assert freshness.expected_fdic_quarter(today) == expected


def fresh_cursor(run_start, latest=date(2026, 6, 30), scored=4257, altered_offset=timedelta(minutes=5)):
    return FakeCursor(
        {
            "INFORMATION_SCHEMA.TABLES": [
                ("DIM_BANK", run_start + altered_offset),
                ("FCT_BANK_QUARTER", run_start + altered_offset),
            ],
            "max(REPORT_DATE)": [(latest,)],
            "MART_EARLY_WARNING": [(scored,)],
        }
    )


def test_freshness_passes_for_a_fresh_run():
    start = datetime(2026, 11, 2, 11, tzinfo=UTC)
    out = freshness.check(fresh_cursor(start), start, date(2026, 11, 2), scored_banks_median=4250)
    assert out["problems"] == [] and out["scored_banks_range"] == [4122, 4378]


def test_freshness_names_every_problem():
    start = datetime(2026, 11, 2, 11, tzinfo=UTC)
    cur = fresh_cursor(start, latest=date(2026, 3, 31), scored=3000, altered_offset=timedelta(days=-7))
    problems = freshness.check(cur, start, date(2026, 11, 2))["problems"]
    assert any("not rebuilt in this run: DIM_BANK, FCT_BANK_QUARTER" in p for p in problems)
    assert any("latest FDIC quarter is 2026-03-31, expected 2026-06-30" in p for p in problems)
    assert any("3,000 scored banks, expected 3,500 to 5,500" in p for p in problems)


# ------------------------------------------------------------------ volume checks
def hist(**kw):
    base = {"SILVER_ROWS": 2_800_000, "GOLD_ROWS": 750_000, "MARTS_ROWS": 3_500_000, "SCORED_BANKS": 4250}
    return {**base, **kw}


# The five prod runs of 29 Oct - 2 Nov 2026 (OPS.PIPELINE_RUNS), oldest first: latest FDIC quarter,
# silver and marts rows, and bronze totals per source as recorded in ROWS_DETAIL. The last one is
# the scheduled run whose total-bronze check fired (+5.61% vs the median of 4) on normal growth.
REAL_RUNS = [
    ("2026-06-30", 2_778_000, 3_509_026, {"cfpb_complaints": 2_146_551, "fdic_failures": 24_702, "fdic_financials": 1_537_308, "fdic_institutions": 167_010}),
    ("2026-06-30", 2_787_583, 3_518_607, {"cfpb_complaints": 2_202_964, "fdic_failures": 28_819, "fdic_financials": 1_545_974, "fdic_institutions": 194_845}),
    ("2026-06-30", 2_787_583, 3_518_607, {"cfpb_complaints": 2_254_310, "fdic_failures": 32_936, "fdic_financials": 1_554_640, "fdic_institutions": 222_680}),
    ("2026-06-30", 2_787_933, 3_518_957, {"cfpb_complaints": 2_303_193, "fdic_failures": 37_053, "fdic_financials": 1_563_306, "fdic_institutions": 250_515}),
    ("2026-06-30", 2_788_226, 3_519_248, {"cfpb_complaints": 2_352_367, "fdic_failures": 41_170, "fdic_financials": 1_571_972, "fdic_institutions": 278_350}),
]  # fmt: skip


def run_row(quarter, silver, marts, bronze, added=None):
    detail = {"bronze": bronze, **({"bronze_added": added} if added else {})}
    return {
        **hist(SILVER_ROWS=silver, GOLD_ROWS=743_802, MARTS_ROWS=marts, SCORED_BANKS=4257),
        "ROWS_DETAIL": detail,
        "LATEST_FDIC_QUARTER": quarter,
    }


def real_history(upto):
    """The successful runs before run number `upto` (0-based), newest first, as fetch_history returns them."""
    return [run_row(*r) for r in reversed(REAL_RUNS[:upto])][: volume_checks.BASELINE_RUNS + 1]


def test_the_five_real_runs_replay_without_an_alert():
    for k in range(1, len(REAL_RUNS)):
        out = volume_checks.compare(run_row(*REAL_RUNS[k]), real_history(k))
        assert out["anomalies"] == [], (k, out)
    # the last run is judged against a real baseline: 3 earlier runs with rows added
    out = volume_checks.compare(run_row(*REAL_RUNS[4]), real_history(4))
    assert out["added"] == {
        "cfpb_complaints": 49_174,
        "fdic_failures": 4_117,
        "fdic_financials": 8_666,
        "fdic_institutions": 27_835,
    }
    assert out["usual_added"] == {
        "cfpb_complaints": 51_346,
        "fdic_financials": 8_666,
        "fdic_institutions": 27_835,
        "fdic_failures": 4_117,
    }


def next_run(**added_overrides):
    """A sixth run after the five real ones, adding the usual rows except where overridden."""
    added = {
        "cfpb_complaints": 50_000,
        "fdic_financials": 8_666,
        "fdic_institutions": 27_835,
        "fdic_failures": 4_117,
    }
    added.update(added_overrides)
    last = REAL_RUNS[-1]
    bronze = {k: last[3][k] + added[k] for k in added}
    return run_row(last[0], last[1], last[2], bronze), real_history(5), added


def test_a_broken_download_adding_nothing_alerts():
    current, history, added = next_run(cfpb_complaints=0)
    anomalies = volume_checks.compare(current, history, current_added=added)["anomalies"]
    assert [a["metric"] for a in anomalies] == ["BRONZE_ADDED.cfpb_complaints"]
    assert volume_checks.describe(anomalies[0]) == (
        "rows added to bronze cfpb_complaints: 0 vs usual 50,260 (0x; limit 0.5x-3x of usual)"
    )


def test_a_partial_download_under_half_the_usual_alerts():
    current, history, added = next_run(fdic_institutions=10_000)
    anomalies = volume_checks.compare(current, history, current_added=added)["anomalies"]
    assert [a["metric"] for a in anomalies] == ["BRONZE_ADDED.fdic_institutions"]


def test_a_tenfold_re_download_alerts():
    current, history, added = next_run(fdic_institutions=278_350)  # the whole history again
    anomalies = volume_checks.compare(current, history, current_added=added)["anomalies"]
    assert [a["metric"] for a in anomalies] == ["BRONZE_ADDED.fdic_institutions"]
    assert anomalies[0]["ratio"] == 10.0


def test_a_new_fdic_quarter_may_add_more_financials_but_nothing_else():
    current, history, added = next_run(fdic_financials=8_666 * 4, cfpb_complaints=500_000)
    out = volume_checks.compare(current, history, current_added=added, new_quarter=True)
    assert [a["metric"] for a in out["anomalies"]] == ["BRONZE_ADDED.cfpb_complaints"]
    assert "new FDIC quarter" in out["note"]
    out = volume_checks.compare(current, history, current_added=added, new_quarter=False)
    assert {a["metric"] for a in out["anomalies"]} == {
        "BRONZE_ADDED.fdic_financials",
        "BRONZE_ADDED.cfpb_complaints",
    }


def test_rows_added_are_worked_out_from_totals_when_not_passed():
    current, history, _ = next_run(cfpb_complaints=0)
    anomalies = volume_checks.compare(current, history)["anomalies"]
    assert [a["metric"] for a in anomalies] == ["BRONZE_ADDED.cfpb_complaints"]


def test_stored_rows_added_are_used_before_totals():
    runs = [run_row(*r, added={"cfpb_complaints": 40_000}) for r in reversed(REAL_RUNS)]
    assert volume_checks.usual_added(runs)["cfpb_complaints"] == 40_000


def test_a_new_quarter_is_detected_from_the_last_successful_run():
    history = real_history(5)
    assert volume_checks.is_new_quarter("2026-09-30", history)
    assert not volume_checks.is_new_quarter("2026-06-30", history)
    assert not volume_checks.is_new_quarter(None, history)


def test_no_baseline_with_fewer_than_three_runs():
    out = volume_checks.compare(hist(), [hist(), hist()])
    assert out["anomalies"] == [] and "no baseline yet (2 earlier successful runs, need 3)" in out["note"]


def test_normal_growth_is_not_an_anomaly():
    history = [hist(), hist(SILVER_ROWS=2_790_000), hist(SILVER_ROWS=2_780_000), hist()]
    current = hist(SILVER_ROWS=2_830_000, MARTS_ROWS=3_540_000, SCORED_BANKS=4240)
    assert volume_checks.compare(current, history)["anomalies"] == []


def test_drops_and_jumps_are_flagged_with_their_limits():
    history = [hist() for _ in range(4)]
    current = hist(SILVER_ROWS=2_700_000, GOLD_ROWS=800_000, SCORED_BANKS=3_900)
    anomalies = {a["metric"]: a for a in volume_checks.compare(current, history)["anomalies"]}
    assert set(anomalies) == {"SILVER_ROWS", "GOLD_ROWS", "SCORED_BANKS"}
    assert anomalies["SILVER_ROWS"]["limit_pct"] == -0.5 and anomalies["GOLD_ROWS"]["limit_pct"] == 5.0
    assert "SCORED_BANKS: 3,900 vs median 4,250 (-8.24%, limit -3.00%)" == volume_checks.describe(
        anomalies["SCORED_BANKS"]
    )


def test_bronze_totals_are_no_longer_compared():
    assert "BRONZE_ROWS" not in volume_checks.RULES


def test_baseline_is_a_median_so_one_odd_week_doesnt_move_it():
    history = [hist(), hist(), hist(), hist(GOLD_ROWS=10)]
    assert volume_checks.baseline(history)["GOLD_ROWS"] == 750_000


def test_history_query_excludes_this_run_and_failed_runs():
    detail = json.dumps({"bronze": {"cfpb_complaints": 1}})
    cur = FakeCursor({"PIPELINE_RUNS": [(1, 2, 3, 4, detail, "2026-06-30")]})
    rows = volume_checks.fetch_history(cur, "this-run")
    sql, params = cur.queries[0]
    assert "STATE = 'success'" in sql and params == {"run_id": "this-run", "limit": 5}
    assert rows[0]["SILVER_ROWS"] == 1 and rows[0]["ROWS_DETAIL"] == {"bronze": {"cfpb_complaints": 1}}


def test_the_run_log_stores_rows_added_per_source():
    outputs = {"bronze": {"cfpb_complaints": {"new_rows": 49_174, "total_rows": 2_352_367, "files": 43}}}
    detail = run_log.layer_rows(outputs)["detail"]
    assert detail["bronze_added"] == {"cfpb_complaints": 49_174}
    assert detail["bronze"] == {"cfpb_complaints": 2_352_367}


# ------------------------------------------------------------------ run log
def test_run_outcome_lists_root_causes_only():
    states = {
        "download_fdic": "success",
        "load_snowflake": "failed",
        "dbt_build.x": "upstream_failed",
        "record_run": None,
    }
    assert run_log.run_outcome(states, "record_run") == ("failed", ["load_snowflake"])
    assert run_log.run_outcome({"a": "success", "b": "skipped"}, "record_run") == ("success", [])


OUTPUTS = {
    "bronze": {"fdic_financials": {"new_rows": 10, "total_rows": 751_322, "files": 2},
               "cfpb_complaints": {"total_rows": 1_983_259}},
    "silver": {"rows": {"fdic_financials": 743_802, "cfpb_complaints": 1_983_259}, "data_quality": {"warnings": 2}},
    "gold": {"bank_quarter_rows": 743_802, "data_quality": {"warnings": 1}},
    "score": {"banks": 4257, "model": "bank_health.ml.distress_model v5 (@champion)", "data_quality": {"warnings": 0}},
}  # fmt: skip


def test_layer_rows_from_notebook_summaries():
    layers = run_log.layer_rows(OUTPUTS)
    assert (layers["BRONZE_ROWS"], layers["SILVER_ROWS"], layers["GOLD_ROWS"]) == (
        2_734_581,
        2_727_061,
        743_802,
    )
    assert layers["DQ_WARNINGS"] == 3
    assert (layers["MODEL_NAME"], layers["MODEL_VERSION"], layers["MODEL_ALIAS"]) == (
        "bank_health.ml.distress_model", "5", "champion")  # fmt: skip
    assert run_log.layer_rows({})["BRONZE_ROWS"] is None  # a run that failed before Databricks


def test_databricks_outputs_reads_each_task():
    class Api:
        def call(self, method, path, params=None):
            if path.endswith("/runs/get"):
                return {
                    "tasks": [{"task_key": "gold", "run_id": 2}, {"task_key": "train_model", "run_id": 3}]
                }
            return {2: {"notebook_output": {"result": json.dumps({"bank_quarter_rows": 5})}}, 3: {}}[
                params["run_id"]
            ]

    assert run_log.databricks_outputs(Api(), 1) == {"gold": {"bank_quarter_rows": 5}}


def test_dbt_counts_by_status():
    cur = FakeCursor(
        {
            "DBT_RESULTS": [
                ("model", "success", 12),
                ("test", "pass", 94),
                ("test", "warn", 2),
                ("test", "error", 1),
            ]
        }
    )
    assert run_log.dbt_counts(cur, "r") == {
        "DBT_MODELS_OK": 12, "DBT_MODELS_ERROR": 0, "DBT_TESTS_PASS": 94, "DBT_TESTS_WARN": 2, "DBT_TESTS_FAIL": 1}  # fmt: skip
    assert run_log.dbt_counts(FakeCursor(), "r") == {}  # no dbt results: NULLs, not zeros


def test_no_metering_rows_means_null_not_zero():
    """Dev has no MONITOR USAGE: Snowflake returns no rows, not an error. That's unknown, not free."""
    cur = FakeCursor({"WAREHOUSE_METERING_HISTORY": [(None, 0)]})
    assert run_log.credits_used(cur, "BANK_DEV_WH", datetime.now(UTC), datetime.now(UTC)) is None


def bare_row(started_at, ended_at, scheduled=True, state="success"):
    return run_log.build_row(
        run_id="r", environment="prod", host_id="oracle-vm", dag_id="d",
        started_at=started_at, ended_at=ended_at, state=state, failed_tasks=[], scheduled=scheduled,
    )  # fmt: skip


def test_an_end_before_the_start_fails_clearly():
    with pytest.raises(
        run_log.InvalidRunTimes,
        match=r"ENDED_AT 2026-10-28 04:48:00Z is before STARTED_AT 2026-10-28 11:00:00Z",
    ):
        bare_row(chicago(2026, 10, 28, 6), chicago(2026, 10, 27, 23, 48))


def test_times_are_stored_in_utc_from_chicago_input():
    # Mon 2 Nov 2026 is the first Monday after clocks go back: Chicago is UTC-6 (CST).
    r = bare_row(chicago(2026, 11, 2, 6), chicago(2026, 11, 2, 6, 25))
    assert r["STARTED_AT"] == datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    assert r["ENDED_AT"] == datetime(2026, 11, 2, 12, 25, tzinfo=UTC)
    assert r["STARTED_AT"].utcoffset() == timedelta(0) and r["ENDED_AT"].utcoffset() == timedelta(0)
    assert r["DURATION_SECONDS"] == 1500


@pytest.mark.parametrize("moment", [None, datetime(2026, 11, 2, 6)])
def test_missing_or_naive_times_are_refused(moment):
    with pytest.raises(run_log.InvalidRunTimes, match="STARTED_AT"):
        bare_row(moment, chicago(2026, 11, 2, 7))


def test_slo_is_judged_only_for_scheduled_runs():
    on_time = (chicago(2026, 11, 2, 6), chicago(2026, 11, 2, 6, 25))
    assert bare_row(*on_time, scheduled=True)["SLO_MET"] is True
    assert bare_row(*on_time, scheduled=False)["SLO_MET"] is None  # manual run: not this week's run
    late = (chicago(2026, 11, 2, 6), chicago(2026, 11, 2, 12, 30))
    assert bare_row(*late, scheduled=True)["SLO_MET"] is False
    title, lines = run_log.success_message(bare_row(*on_time, scheduled=False))
    assert "Data ready before Monday 12:00 CT: n/a (manual run)" in lines


def test_credits_are_none_without_the_privilege():
    cur = FakeCursor({"WAREHOUSE_METERING_HISTORY": PermissionError("insufficient privileges")})
    assert run_log.credits_used(cur, "BANK_DEV_WH", datetime.now(UTC), datetime.now(UTC)) is None
    assert run_log.credits_used(FakeCursor({"WAREHOUSE_METERING_HISTORY": [(0.0833, 1)]}), "BANK_WH",
                                datetime.now(UTC), datetime.now(UTC)) == 0.0833  # fmt: skip


def row(state="success", **kw):
    return run_log.build_row(
        run_id="scheduled__2026-11-02", environment="prod", host_id="oracle-vm", dag_id="bank_health_weekly",
        started_at=chicago(2026, 11, 2, 6), ended_at=chicago(2026, 11, 2, 6, 24), state=state,
        failed_tasks=kw.pop("failed", []), layers=run_log.layer_rows(OUTPUTS),
        marts={"DIM_BANK": 27_835, "MART_EARLY_WARNING": 4257}, dbt={"DBT_TESTS_PASS": 94, "DBT_TESTS_WARN": 2},
        credits=0.08333, latest_quarter=date(2026, 6, 30), scheduled=kw.pop("scheduled", True), **kw,
    )  # fmt: skip


def test_build_row_fills_every_column():
    r = row()
    assert set(r) == set(run_log.COLUMNS)
    assert (r["DURATION_SECONDS"], r["MARTS_ROWS"], r["SCORED_BANKS"], r["SLO_MET"]) == (
        1440,
        32_092,
        4257,
        True,
    )
    assert r["ROWS_DETAIL"]["marts"]["MART_EARLY_WARNING"] == 4257 and r["CREDITS_ESTIMATE"] == 0.0833
    failed = row(state="failed", failed=["load_snowflake", "verify_pipeline"])
    assert failed["FAILED_TASKS"] == "load_snowflake, verify_pipeline" and failed["SLO_MET"] is False


class MergeTable:
    """Stands in for OPS.PIPELINE_RUNS: applies a MERGE on RUN_ID like Snowflake would."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        assert sql.startswith("merge into OPS.PIPELINE_RUNS t using (select ")
        assert "on t.RUN_ID = s.RUN_ID" in sql and "when matched then update" in sql
        assert "when not matched then insert" in sql
        self.rows[params["RUN_ID"]] = dict(params)  # matched: replace; not matched: insert


def test_write_row_is_one_merge_that_parses_json():
    table = MergeTable()
    run_log.write_row(table, row(volume_anomalies=[{"metric": "GOLD_ROWS"}]))
    (sql,) = table.statements  # one atomic statement: no DELETE + INSERT window
    assert "parse_json(%(ROWS_DETAIL)s) as ROWS_DETAIL" in sql
    assert "parse_json(%(VOLUME_ANOMALIES)s) as VOLUME_ANOMALIES" in sql
    assert json.loads(table.rows["scheduled__2026-11-02"]["VOLUME_ANOMALIES"]) == [{"metric": "GOLD_ROWS"}]


def test_writing_one_run_twice_leaves_one_row():
    """record_run retried by Airflow (e.g. after a timeout) must not create a second row."""
    table = MergeTable()
    run_log.write_row(table, row(state="failed", failed=["load_snowflake"]))
    run_log.write_row(table, row(state="success"))
    assert list(table.rows) == ["scheduled__2026-11-02"]
    assert table.rows["scheduled__2026-11-02"]["STATE"] == "success"  # the retry's values win


def test_merge_updates_every_column_except_the_key():
    sql = run_log.merge_sql()
    for column in run_log.COLUMNS:
        if column != "RUN_ID":
            assert f"t.{column} = s.{column}" in sql
    assert "t.RUN_ID = s.RUN_ID," not in sql  # the key is matched on, never overwritten


def test_backfill_final_credits_updates_settled_runs():
    start, end = chicago(2026, 10, 26, 6), chicago(2026, 10, 26, 6, 30)
    cur = FakeCursor(
        {"CREDITS_FINAL is null": [("r1", start, end)], "WAREHOUSE_METERING_HISTORY": [(0.1, 1)]}
    )
    assert run_log.backfill_final_credits(cur, "BANK_WH", chicago(2026, 11, 2, 7)) == 1
    assert any(sql.startswith("update OPS.PIPELINE_RUNS set CREDITS_FINAL") for sql, _ in cur.queries)


def test_success_message_says_what_ran():
    title, lines = run_log.success_message(row())
    text = "\n".join(lines)
    assert title == "OK: bank_health_weekly succeeded"
    assert "24 min" in text and "silver 2,727,061" in text and "Scored banks: 4,257" in text
    assert "dbt: 94 tests passed, 2 warned, n/a failed" in text and "before Monday 12:00 CT: yes" in text
