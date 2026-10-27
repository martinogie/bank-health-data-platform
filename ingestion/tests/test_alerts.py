"""Unit tests for ingestion/alerts.py. Both webhooks are fakes; nothing is sent anywhere."""

from __future__ import annotations

import logging
import types

import pytest
import requests

from ingestion import alerts

SLACK = "https://hooks.slack.com/services/T000/B000/secretSLACKtoken123"
DISCORD = "https://discord.com/api/webhooks/123456/secretDISCORDtoken456"


class Resp:
    def __init__(self, status):
        self.status_code = status


class FakeHttp:
    """Records posts; `outcome[name]` is a status code, or an exception to raise."""

    def __init__(self, slack=200, discord=204):
        self.outcome = {SLACK: slack, DISCORD: discord}
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json, timeout))
        out = self.outcome[url]
        if isinstance(out, Exception):
            raise out
        return Resp(out)


@pytest.fixture
def webhooks(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", SLACK)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", DISCORD)


def assert_no_secret(caplog):
    text = caplog.text
    assert "secretSLACKtoken123" not in text and "secretDISCORDtoken456" not in text
    assert "hooks.slack.com/services" not in text and "api/webhooks" not in text


def test_both_channels_receive_the_same_message(webhooks, caplog):
    http = FakeHttp()
    assert alerts.notify("Run finished", ["rows: 10"], env="dev", session=http) == {
        "Slack": True,
        "Discord": True,
    }
    (s_url, s_body, s_timeout), (d_url, d_body, _) = http.posts
    assert (s_url, d_url) == (SLACK, DISCORD)
    assert s_body["text"] == d_body["content"] == "[DEV] Run finished\nrows: 10"
    assert d_body["allowed_mentions"] == {"parse": []}  # no @everyone surprises
    assert s_timeout == alerts.TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("slack", "discord", "expected", "failed"),
    [
        (500, 204, {"Slack": False, "Discord": True}, ["Slack"]),
        (
            200,
            requests.ConnectionError(f"cannot reach {DISCORD}"),
            {"Slack": True, "Discord": False},
            ["Discord"],
        ),
        (
            requests.Timeout(f"timed out {SLACK}"),
            404,
            {"Slack": False, "Discord": False},
            ["Slack", "Discord"],
        ),
    ],
)
def test_one_channel_failing_never_stops_the_other(webhooks, caplog, slack, discord, expected, failed):
    caplog.set_level(logging.WARNING)
    http = FakeHttp(slack=slack, discord=discord)
    assert alerts.notify("x", env="prod", session=http) == expected
    assert len(http.posts) == 2  # both were tried
    for name in failed:
        assert f"alert to {name} failed" in caplog.text
    assert_no_secret(caplog)


def test_missing_webhook_is_a_warning_not_an_error(monkeypatch, caplog):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", DISCORD)
    caplog.set_level(logging.WARNING)
    http = FakeHttp()
    assert alerts.notify("x", session=http) == {"Slack": False, "Discord": True}
    assert "SLACK_WEBHOOK_URL is not set" in caplog.text


def test_notify_never_raises_even_on_broken_input(webhooks, caplog):
    class Boom:
        def __str__(self):
            raise RuntimeError("cannot format")

    assert alerts.notify(Boom(), session=FakeHttp()) == {"Slack": False, "Discord": False}  # type: ignore[arg-type]


def test_secrets_are_masked_in_the_message_itself(webhooks):
    http = FakeHttp()
    alerts.notify("Error", [f"requests failed for {SLACK} and {DISCORD}"], session=http)
    body = http.posts[0][1]["text"]
    assert "secret" not in body and body.count("***") == 2


def test_mask_catches_any_webhook_url():
    other = "https://hooks.slack.com/services/T9/B9/not-ours"
    assert alerts.mask(f"x {other} y", secrets=[]) == "x *** y"
    assert alerts.mask("https://discordapp.com/api/webhooks/1/abc", secrets=[]) == "***"


def test_long_messages_are_cut_for_discord(webhooks):
    http = FakeHttp()
    alerts.notify("x", ["y" * 5000], session=http)
    assert len(http.posts[1][1]["content"]) <= alerts.DISCORD_LIMIT
    assert http.posts[1][1]["content"].endswith("(cut)")


@pytest.mark.parametrize(
    "text",
    [
        "Run failed: QUOTA_EXCEEDED for workspace",
        "Your workspace has reached its daily compute limit",
        "Serverless usage limit has been reached for today",
        "RESOURCE_EXHAUSTED: Free Edition quota",
    ],
)
def test_quota_errors_are_recognised(text):
    assert alerts.is_databricks_quota_error(text)


@pytest.mark.parametrize("text", ["Notebook raised ValueError", "Snowflake: table not found", ""])
def test_other_errors_are_not_quota_errors(text):
    assert not alerts.is_databricks_quota_error(text)


def test_failure_message_has_everything_needed(monkeypatch):
    monkeypatch.setenv("AIRFLOW_UI_URL", "http://localhost:8080")
    lines = alerts.failure_lines(
        dag_id="bank_health_weekly",
        run_id="scheduled__2026-11-02T12:00:00+00:00",
        task_id="run_databricks_job",
        started="2026-11-02 06:00",
        try_number=2,
        total_tries=2,
        error="AirflowException: Job run failed\nQUOTA_EXCEEDED\nline 3",
    )
    text = "\n".join(lines)
    assert "Failed task: run_databricks_job (try 2 of 2)" in text
    assert alerts.QUOTA_LINE in text
    assert "Run: scheduled__2026-11-02T12:00:00+00:00" in text
    assert (
        "Log: http://localhost:8080/dags/bank_health_weekly/runs/scheduled__2026-11-02T12%3A00%3A00%2B00%3A00"
        "/tasks/run_databricks_job?try_number=2" in text
    )


def test_failure_callback_sends_one_alert_and_never_raises(webhooks, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "notify", lambda title, lines=None, **k: sent.append((title, lines)))
    context = {
        "ti": types.SimpleNamespace(dag_id="d", run_id="r", task_id="t", try_number=3, start_date="s"),
        "task": types.SimpleNamespace(retries=2),
        "dag_run": types.SimpleNamespace(start_date="2026-11-02"),
        "exception": RuntimeError("Snowflake said no"),
    }
    alerts.task_failure_callback(context)
    ((title, lines),) = sent
    assert title == "FAILED: d / t"
    assert "Failed task: t (try 3 of 3)" in lines
    alerts.task_failure_callback({})  # an empty context still doesn't raise


def test_failure_callback_names_the_quota(webhooks, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "notify", lambda title, lines=None, **k: sent.append(title))
    alerts.task_failure_callback(
        {
            "ti": types.SimpleNamespace(task_id="run_databricks_job"),
            "exception": Exception("daily quota reached"),
        }
    )
    assert sent == ["FAILED: ? / run_databricks_job: Databricks Free Edition daily quota reached"]
