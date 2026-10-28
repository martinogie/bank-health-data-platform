"""
### Bank Health — late-run check (SLO)

**Objective:** this week's data is ready by **Monday 12:00 America/Chicago**: the week's
`bank_health_weekly` run has succeeded (a `PIPELINE_RUNS` row with state `success`) and finished
before noon.

This DAG runs every Monday at 12:00 CT. If no on-time successful run is logged, it posts a
**LATE** alert to Slack and Discord and fails. In prod it runs only on the machine named in
`OPS.PROD_SCHEDULER` (one scheduler only); elsewhere it skips.

The GitHub Actions watchdog (`.github/workflows/late_run_watchdog.yml`) makes the same check
from outside, so a late run is reported even when this whole machine is down.

**Test in dev** (it posts to the dev channels): trigger with `{"pretend_now": "2026-11-02T12:05:00-06:00"}`
to judge the week as if it were that moment. With no successful dev run that week, it alerts.
"""

from __future__ import annotations

import os

import pendulum
from airflow.sdk import Param, dag, task

from ingestion import environment

ENV = environment.settings()
TZ = "America/Chicago"


@dag(
    dag_id="bank_health_slo_check",
    schedule="0 12 * * 1",
    start_date=pendulum.datetime(2026, 10, 1, tz=TZ),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": pendulum.duration(minutes=5)},
    params={
        "pretend_now": Param(
            None,
            type=["null", "string"],
            description="Testing only: an ISO date-time to judge the week as if it were that moment.",
        )
    },
    doc_md=__doc__,
    tags=["bank-health", ENV.name, "slo", "alerts"],
)
def bank_health_slo_check():
    @task(doc_md="Was this week's run successful by Monday 12:00 CT? Alerts (and fails) if not.")
    def check_slo() -> dict:
        import logging
        from datetime import datetime

        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook
        from airflow.sdk import get_current_context
        from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException

        from ingestion import scheduler_guard, slo
        from ingestion.alerts import notify

        pretend = get_current_context()["params"].get("pretend_now")
        now = datetime.fromisoformat(pretend) if pretend else pendulum.now("UTC")
        logging.getLogger("snowflake").setLevel(logging.ERROR)  # login URL contains the account
        conn = SnowflakeHook(snowflake_conn_id="snowflake_default").get_conn()
        try:
            cur = conn.cursor()
            if ENV.is_prod:
                try:
                    scheduler_guard.check(cur, os.environ.get("PROD_HOST_ID"))
                except scheduler_guard.SchedulerNotAuthorized as exc:
                    raise AirflowSkipException(f"not the prod scheduler: {exc}") from None
            ok, message = slo.check_week(cur, now, table=f"{ENV.snowflake_database}.OPS.PIPELINE_RUNS")
        finally:
            conn.close()
        print(message)
        if not ok:
            notify(
                "LATE: this week's bank data is not ready (objective: Monday 12:00 CT)",
                [message, "What to do: docs/runbooks/alerts.md, 'Late run'."],
            )
            raise AirflowFailException(message)  # no retry: the answer won't change in 5 minutes
        return {"ok": ok, "message": message}

    check_slo()


bank_health_slo_check()
