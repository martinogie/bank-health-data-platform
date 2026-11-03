"""Credential expiry checks (O-05) and Databricks minutes per run (C-02). Offline."""

from datetime import date
from pathlib import Path

from ingestion import credentials, run_log

TODAY = date(2026, 11, 3)


def test_rotate_by_date_from_set_on_or_due():
    assert credentials.deadline({"name": "k", "set_on": "2026-05-10"}) == date(2026, 11, 6)  # +180 days
    assert credentials.deadline({"name": "k", "set_on": "2026-10-01", "rotate_days": 30}) == date(
        2026, 10, 31
    )
    assert credentials.deadline({"name": "ip", "due": "2026-11-20"}) == date(2026, 11, 20)
    assert credentials.deadline({"name": "k", "set_on": None}) is None


def test_only_items_within_14_days_or_overdue_are_due_soonest_first():
    entries = [
        {"name": "far", "due": "2026-12-31"},
        {"name": "soon", "due": "2026-11-15", "how": "rotation.md"},
        {"name": "late", "set_on": "2026-05-01"},  # rotate by 28 Oct: overdue
    ]
    assert credentials.due(entries, [], TODAY) == [
        "late: due 2026-10-28 (6 day(s) overdue)",
        "soon: due 2026-11-15 (in 12 day(s)); how: rotation.md",
    ]


def test_a_missing_date_is_reported_not_hidden():
    assert credentials.due([{"name": "key", "set_on": None}], [], TODAY) == [
        "key: no date recorded in ops/credentials.yml"
    ]


def test_databricks_token_expiry_from_the_api():
    infos = [
        {
            "token_id": "a",
            "comment": "airflow prod",
            "expiry_time": 1_763_164_800_000,
        },  # 2025-11-15 UTC: expired
        {"token_id": "b", "comment": "github bundle deploy", "expiry_time": 1_794_268_800_000},  # 2026-11-10
        {"token_id": "c", "comment": "no expiry", "expiry_time": -1},
    ]
    tokens = credentials.token_deadlines(infos)
    assert [t["deadline"] for t in tokens] == [date(2025, 11, 15), date(2026, 11, 10)]
    lines = credentials.due([], tokens, TODAY)
    assert lines[0].startswith("Databricks token 'airflow prod': due 2025-11-15 (353 day(s) overdue)")
    assert lines[1].startswith("Databricks token 'github bundle deploy': due 2026-11-10 (in 7 day(s))")


def test_the_repo_file_loads_and_has_the_home_ip_reminder():
    entries = credentials.load(Path(__file__).resolve().parents[2] / "ops" / "credentials.yml")
    reminder = [e for e in entries if "home IP" in e["name"]]
    assert reminder and credentials.deadline(reminder[0]) == date(2026, 11, 20)
    assert all("name" in e for e in entries)


def test_databricks_minutes_per_run():
    run = {"tasks": [
        {"task_key": "bronze", "setup_duration": 3000, "execution_duration": 118000, "cleanup_duration": 0},
        {"task_key": "silver", "setup_duration": 1000, "execution_duration": 92000, "cleanup_duration": None},
    ]}  # fmt: skip
    assert run_log.run_minutes(run) == {"total": 3.6, "tasks": {"bronze": 2.02, "silver": 1.55}}


def test_minutes_go_to_the_run_row_and_the_message():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ct = ZoneInfo("America/Chicago")
    row = run_log.build_row(
        run_id="r", environment="prod", host_id="oracle-vm", dag_id="bank_health_weekly",
        started_at=datetime(2026, 11, 9, 6, tzinfo=ct), ended_at=datetime(2026, 11, 9, 6, 25, tzinfo=ct),
        state="success", failed_tasks=[], scheduled=True,
        databricks_minutes={"total": 7.1, "tasks": {"bronze": 2.0}},
    )  # fmt: skip
    assert row["DATABRICKS_MINUTES"] == 7.1 and row["ROWS_DETAIL"]["databricks_minutes"] == {"bronze": 2.0}
    assert "Databricks: 7.1 min" in run_log.success_message(row)[1]
