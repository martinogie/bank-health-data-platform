"""Alerts to Slack and Discord: one message, both channels.

Every alert goes through notify(): it formats one message and posts it to a Slack incoming
webhook and a Discord channel webhook. The webhook URLs are secrets (SLACK_WEBHOOK_URL and
DISCORD_WEBHOOK_URL in each environment's .env; dev and prod have their own webhooks and
channels). Rules:
- If one channel fails, the other is still tried; a warning names the failed channel.
- An alert never fails the task that sends it: notify() catches everything and returns a
  per-channel result instead.
- A webhook URL never appears in a log or an error message: mask() replaces it.

What sends an alert: a task failing after its last retry (task_failure_callback), the freshness
and volume checks, the Monday 12:00 CT late-run check, and one success message per run.
Setup and what to do for each alert: docs/runbooks/alerts.md.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

CHANNELS = {"Slack": "SLACK_WEBHOOK_URL", "Discord": "DISCORD_WEBHOOK_URL"}
TIMEOUT_SECONDS = 10
DISCORD_LIMIT = 2000  # characters per Discord message
SLACK_LIMIT = 3500  # Slack allows more; long alerts are cut so both channels show the same text
ERROR_LINES = 8

# Any Slack or Discord webhook URL, even one that isn't in this process's environment.
_WEBHOOK_RE = re.compile(
    r"https?://(?:hooks\.slack\.com/\S+|(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\S+)", re.I
)

# Phrases Databricks uses when the Free Edition daily compute quota stops a run. The exact
# wording isn't documented, so this list is deliberately broad; tests keep it in one place.
_QUOTA_RE = re.compile(
    r"quota|usage limit|daily (?:compute )?limit|limit (?:has been )?(?:reached|exceeded)"
    r"|resource[_ ]exhausted|exceeded (?:the|your) (?:free|daily)",
    re.I,
)
QUOTA_LINE = "Databricks Free Edition daily quota reached"
QUOTA_RUNBOOK = "docs/runbooks/quota_errors.md#databricks-free-edition-daily-quota"

# Snowflake stops a warehouse whose resource monitor has used its credit quota, e.g.
#   090073 (22000): Warehouse 'BANK_DEV_WH' cannot be resumed because resource monitor
#   'BANK_DEV_WH_MONITOR' has exceeded its quota.
# It contains the word "quota" too, so it is recognised first and never called a Databricks stop.
_SNOWFLAKE_MONITOR_RE = re.compile(
    r"\b090073\b|resource monitor\s+'?(?P<monitor>[\w$]+)'?\s+has exceeded its quota", re.I
)
_MONITOR_NAME_RE = re.compile(r"resource monitor\s+'?(?P<monitor>[\w$]+)'?", re.I)
SNOWFLAKE_CAP_LINE = "Snowflake credit cap reached"
SNOWFLAKE_CAP_RUNBOOK = "docs/runbooks/quota_errors.md#snowflake-credit-cap-reached"


def mask(text: Any, secrets: list[str] | None = None) -> str:
    """Text with every webhook URL (and any given secret) replaced by ***."""
    out = str(text)
    for value in secrets if secrets is not None else _webhook_urls():
        if value:
            out = out.replace(value, "***")
    return _WEBHOOK_RE.sub("***", out)


def _webhook_urls() -> list[str]:
    return [os.environ.get(var, "").strip() for var in CHANNELS.values()]


def snowflake_monitor_name(text: Any) -> str | None:
    """The resource monitor named in a Snowflake credit-cap error ('?' if unnamed), else None."""
    text = str(text or "")
    if not _SNOWFLAKE_MONITOR_RE.search(text):
        return None
    m = _MONITOR_NAME_RE.search(text)
    return m.group("monitor") if m else "?"


def is_databricks_quota_error(text: Any) -> bool:
    return snowflake_monitor_name(text) is None and bool(_QUOTA_RE.search(str(text or "")))


def quota_reason(text: Any) -> tuple[str, str] | None:
    """(label, runbook section) when an error is a credit or compute cap, else None."""
    monitor = snowflake_monitor_name(text)
    if monitor is not None:
        return f"{SNOWFLAKE_CAP_LINE} ({monitor})", SNOWFLAKE_CAP_RUNBOOK
    if is_databricks_quota_error(text):
        return QUOTA_LINE, QUOTA_RUNBOOK
    return None


def first_lines(text: Any, n: int = ERROR_LINES, width: int = 300) -> str:
    lines = [line.rstrip()[:width] for line in str(text or "").strip().splitlines() if line.strip()]
    return "\n".join(lines[:n])


def environment_tag(env: str | None = None) -> str:
    return (env or os.environ.get("BANK_HEALTH_ENV") or "unknown").strip().upper()


def notify(
    title: str,
    lines: list[str] | None = None,
    *,
    env: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, bool]:
    """Send one message to Slack and Discord. Returns {"Slack": ok, "Discord": ok}. Never raises."""
    results = {name: False for name in CHANNELS}
    try:
        text = f"[{environment_tag(env)}] {title}"
        if lines:
            text += "\n" + "\n".join(line for line in lines if line)
        secrets = _webhook_urls()
        text = mask(text, secrets)  # an error message could quote a URL
        http = session or requests.Session()
        for name, var in CHANNELS.items():
            url = os.environ.get(var, "").strip()
            if not url:
                log.warning("alert not sent to %s: %s is not set", name, var)
                continue
            payload: dict[str, Any]
            if name == "Slack":
                payload = {"text": _cut(text, SLACK_LIMIT)}
            else:
                payload = {"content": _cut(text, DISCORD_LIMIT), "allowed_mentions": {"parse": []}}
            try:
                resp = http.post(url, json=payload, timeout=TIMEOUT_SECONDS)
                if resp.status_code >= 400:
                    log.warning("alert to %s failed: HTTP %s", name, resp.status_code)
                    continue
                results[name] = True
            except Exception as exc:  # network errors, timeouts, anything
                log.warning("alert to %s failed: %s", name, mask(f"{type(exc).__name__}: {exc}", secrets))
        sent = [name for name, ok in results.items() if ok]
        if sent:
            log.info("alert sent to %s", ", ".join(sent))  # channel names only, never the URL
    except Exception as exc:  # formatting problems must not fail the caller either
        log.warning("alert not sent: %s", mask(f"{type(exc).__name__}: {exc}"))
    return results


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 20].rstrip() + "\n… (cut)"


def task_log_url(dag_id: str, run_id: str, task_id: str, try_number: int | None) -> str:
    """Link to the task's log in the Airflow UI (through the SSH tunnel on the VM)."""
    base = os.environ.get("AIRFLOW_UI_URL", "http://localhost:8080").rstrip("/")
    url = f"{base}/dags/{quote(dag_id)}/runs/{quote(run_id, safe='')}/tasks/{quote(task_id)}"
    return url + (f"?try_number={try_number}" if try_number else "")


def failure_lines(
    *,
    dag_id: str,
    run_id: str,
    task_id: str,
    started: Any,
    try_number: int | None,
    total_tries: int | None,
    error: Any,
) -> list[str]:
    """The body of a task-failure alert."""
    lines = [
        f"DAG: {dag_id}",
        f"Run: {run_id}",
        f"Failed task: {task_id} (try {try_number or '?'} of {total_tries or '?'})",
        f"Started: {started}",
    ]
    reason = quota_reason(error)
    if reason:
        lines.append(f"{reason[0]}. See {reason[1]}.")
    lines.append("Error:\n" + (first_lines(error) or "(no message)"))
    lines.append(f"Log: {task_log_url(dag_id, run_id, task_id, try_number)}")
    return lines


class AlreadyReported(Exception):  # noqa: N818 (a marker, not an error name)
    """Mixin for exceptions raised only to set a state, whose cause was already alerted.

    record_run raises one to mark the whole run failed after an earlier task failed: that
    task already sent its alert, so the callback stays quiet instead of alerting twice."""


def task_failure_callback(context: Mapping[str, Any]) -> None:
    """on_failure_callback for every task: Airflow calls it once, after the last retry failed."""
    try:
        ti = context.get("ti") or context.get("task_instance")
        task = context.get("task")
        dag_run = context.get("dag_run")
        exc = context.get("exception")
        if isinstance(exc, AlreadyReported):
            return
        dag_id = str(getattr(ti, "dag_id", None) or getattr(dag_run, "dag_id", None) or "?")
        run_id = str(getattr(ti, "run_id", None) or getattr(dag_run, "run_id", None) or "?")
        task_id = str(getattr(ti, "task_id", None) or "?")
        retries = getattr(task, "retries", None)
        error = f"{type(exc).__name__}: {exc}" if exc is not None else "(no exception in context)"
        title = f"FAILED: {dag_id} / {task_id}"
        reason = quota_reason(error)
        if reason:
            title = f"{title}: {reason[0]}"
        notify(
            title,
            failure_lines(
                dag_id=dag_id,
                run_id=run_id,
                task_id=task_id,
                started=getattr(dag_run, "start_date", None) or getattr(ti, "start_date", None),
                try_number=getattr(ti, "try_number", None),
                total_tries=(retries + 1) if isinstance(retries, int) else None,
                error=error,
            ),
        )
    except Exception as exc:  # an alert must never fail the task
        log.warning("failure alert not sent: %s", mask(f"{type(exc).__name__}: {exc}"))
