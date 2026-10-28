"""
### Bank Health — alert test (dev only)

One task that fails on purpose, with the same failure alert as the weekly pipeline
(`ingestion.alerts.task_failure_callback`). Trigger it by hand to check that a failure reaches
**both dev channels** (Slack and Discord) within a minute. It reads and writes no data.

This DAG exists only when `BANK_HEALTH_ENV=dev`: it never appears in prod, so it can't post to the
prod channels. See docs/runbooks/alerts.md, "Test the alerts".
"""

from __future__ import annotations

import pendulum

from ingestion import environment
from ingestion.alerts import task_failure_callback

if environment.current_env() == "dev":
    from airflow.sdk import dag, task

    @dag(
        dag_id="bank_health_alert_test",
        schedule=None,
        start_date=pendulum.datetime(2026, 10, 1, tz="America/Chicago"),
        catchup=False,
        default_args={"retries": 0, "on_failure_callback": task_failure_callback},
        doc_md=__doc__,
        tags=["bank-health", "dev", "alerts"],
    )
    def bank_health_alert_test():
        @task(doc_md="Fails on purpose so the failure alert can be checked end to end.")
        def fail_on_purpose() -> None:
            raise RuntimeError(
                "Deliberate failure from bank_health_alert_test (dev): checking the alert path."
            )

        fail_on_purpose()

    bank_health_alert_test()
