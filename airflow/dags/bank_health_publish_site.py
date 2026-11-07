"""bank_health_publish_site: refresh the website's data after a successful weekly run.

The site (finance.martinsogiekpolor.com/bankhealth) reads its numbers from a private Cloudflare R2
bucket through the router Worker. This DAG writes that data; it never touches the pipeline's tables.

| Task | What it does |
|---|---|
| `publish_site` | Reads the latest successful weekly run and the marts (Snowflake), builds the five data files for every bank (ingestion/site_export.py) plus the dbt docs, validates every file against the schemas, scans every file for identifiers and secrets, uploads to a new runs/<run key>/ prefix, checks every object arrived, switches current.json, and keeps the newest 5 runs. |

**When it runs:** after `bank_health_weekly` succeeds (its last task, `record_run`, emits the asset
`bank_health_weekly_success` only on a fully successful run), and when triggered by hand.

**If it fails:** Slack and Discord get one alert (after the retry); the site keeps showing the
previous run, and the weekly run stays green. Fix, then trigger this DAG again.

**Rollback** (runbook docs/runbooks/site_publish.md): `python -m ingestion.site_publish rollback <run>`.

Dev publishes under the bucket's dev/ prefix, which the site never serves.
"""

from __future__ import annotations

from pathlib import Path

import pendulum
from airflow.sdk import Asset, dag, task

from ingestion import environment
from ingestion.alerts import task_failure_callback
from ingestion.site_publish import WEEKLY_SUCCESS_ASSET

ENV = environment.settings()
DBT_TARGET = Path("/opt/airflow/dbt/target")


@dag(
    dag_id="bank_health_publish_site",
    schedule=[Asset(WEEKLY_SUCCESS_ASSET)],
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Chicago"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 1,
        "retry_delay": pendulum.duration(minutes=5),
        "on_failure_callback": task_failure_callback,
    },
    doc_md=__doc__,
    tags=["bank-health", ENV.name, "website", "r2"],
)
def bank_health_publish_site():
    @task(
        execution_timeout=pendulum.duration(minutes=30),
        doc_md="Build, check and publish the website's data and dbt docs to R2; switch current.json last.",
    )
    def publish_site() -> dict:
        import logging
        import os

        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

        from ingestion import scheduler_guard, site_export, site_publish
        from ingestion.alerts import notify

        logging.getLogger("snowflake").setLevel(logging.ERROR)  # the login URL contains the account
        logging.getLogger("botocore").setLevel(logging.WARNING)
        conn = SnowflakeHook(snowflake_conn_id="snowflake_default").get_conn()
        try:
            if ENV.is_prod:
                cur = conn.cursor()
                try:
                    scheduler_guard.check(cur, os.environ.get("PROD_HOST_ID") or None)
                finally:
                    cur.close()
            inputs = site_export.fetch(conn)
        finally:
            conn.close()

        run = inputs["run"]
        docs = site_export.build_site(
            inputs["watchlist"], inputs["waterfalls"], inputs["ni_rows"], run, inputs["health"],
            site_export.load_model(), fixture=False,
        )  # fmt: skip
        dbt = site_publish.dbt_docs(DBT_TARGET)
        if not dbt:
            raise site_publish.PublishRefused(f"dbt docs not found in {DBT_TARGET} (dbt_docs_generate)")
        root = "" if ENV.is_prod else "dev/"
        key = site_publish.run_key(run["run_id"], run["started_at"])
        meta = inputs["watchlist"]["meta"]
        out = site_publish.publish(
            site_publish.client(), root, key, docs, dbt,
            {"quarter": meta["quarterLabel"], "weeklyRun": run["run_id"]},
        )  # fmt: skip
        banks = sum(1 for p in docs if p.startswith("banks/"))
        print(f"published {key} to {root or '(prod root)'}: {out['files']} files, {banks} banks, "
              f"previous {out['previous']}, deleted {len(out['deleted'])} old run(s)")  # fmt: skip
        notify(
            "Website data published",
            [
                f"{meta['quarterLabel']} · {banks:,} banks · {out['files']:,} files",
                f"run {key} (was {out['previous']})",
            ],
        )
        return {
            "run": key,
            "files": out["files"],
            "banks": banks,
            "previous": out["previous"],
            "deleted": out["deleted"],
        }

    publish_site()


bank_health_publish_site()
