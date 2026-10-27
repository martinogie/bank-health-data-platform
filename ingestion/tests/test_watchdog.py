"""Unit tests for scripts/late_run_watchdog.py: exit codes and alerts, with Snowflake faked."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from ingestion import slo

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "late_run_watchdog.py"
spec = importlib.util.spec_from_file_location("late_run_watchdog", SCRIPT)
watchdog = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(watchdog)  # type: ignore[union-attr]

NOON_PAST = datetime(2026, 11, 2, 12, 30, tzinfo=slo.TZ)


class Cur:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, rows):
        self.cur = Cur(rows)
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(watchdog, "notify", lambda title, lines=None, env=None: out.append((title, env)))
    return out


def use(monkeypatch, rows=None, error=None):
    def connect():
        if error:
            raise error
        return Conn(rows or [])

    monkeypatch.setattr("ingestion.snowflake_load.connect_from_env", connect)


def test_on_time_is_quiet(monkeypatch, sent):
    use(monkeypatch, [("r", "success", NOON_PAST.replace(hour=6), NOON_PAST.replace(hour=6, minute=25))])
    assert watchdog.main(NOON_PAST) == 0 and sent == []


def test_late_alerts_the_prod_channels(monkeypatch, sent):
    use(monkeypatch, [])
    assert watchdog.main(NOON_PAST) == 1
    ((title, env),) = sent
    assert title.startswith("LATE (seen from GitHub)") and env == "prod watchdog"


def test_cannot_check_alerts_without_leaking_secrets(monkeypatch, sent, capsys):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/secretX")
    use(monkeypatch, error=RuntimeError("login failed via https://hooks.slack.com/services/T/B/secretX"))
    assert watchdog.main(NOON_PAST) == 2
    assert sent[0][0] == "WATCHDOG: could not check this week's run"
    assert "secretX" not in capsys.readouterr().out
