"""
### Bank Health — weekly pipeline

Every Monday 06:00 America/Chicago. Every task is safe to re-run (clear it, or the whole run):

| Task | What it does | Why a re-run is safe |
|---|---|---|
| `download_fdic` | new FDIC quarters (last 2 re-pulled for amendments), institutions, failures | fixed file names; complete quarters already on disk are reused |
| `download_cfpb` | complaints since the watermark (30-day look-back), credit bureaus excluded | complete months already on disk are reused |
| `upload_raw` | fresh files -> Databricks Volume; reused files only if the Volume lacks them | uploads overwrite |
| `save_watermarks` | remember how far we got (after the upload succeeded) | writes the same values again |
| `decide_retrain` | retrain only if a new FDIC quarter arrived or `retrain_model` is true | pure decision |
| `sync_databricks_job` | pull the Git folder, create/update job `bank_health_pipeline` | both idempotent |
| `run_databricks_job` | bronze -> silver -> gold -> train (only if `decide_retrain` says so) -> score | Auto Loader checkpoints; silver/gold/scores overwrite; job allows 1 run at a time |
| `load_snowflake` | Databricks exports -> Snowflake `RAW` | each table replaced by an atomic swap |
| `sanity_check_top20` | fails if the 20 riskiest scored banks include a new bank, a foreign-bank office or a 0% capital ratio | read-only |
| `dbt_build` | Cosmos: one task per dbt model with its tests, plus source freshness | dbt rebuilds models |
| `dbt_docs_generate` | dbt docs site (for GitHub Pages) | regenerates files |
| `verify_pipeline` | end-to-end checks in Snowflake | read-only |
| `export_watchlist` | refresh `site/data/watchlist.json` for the watchlist page | file replaced atomically |

The model retrains automatically when a new FDIC quarter arrives; trigger with
`{"retrain_model": true}` to force a retrain. A retrained model is registered as `@candidate`;
scoring keeps using `@champion` until a person reviews the candidate and promotes it
(docs/runbook.md).
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pendulum
from airflow.providers.databricks.operators.databricks import DatabricksRunNowOperator
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import Param, Variable, dag, task
from cosmos import DbtTaskGroup, ExecutionConfig, ProfileConfig, ProjectConfig, RenderConfig
from cosmos.constants import LoadMode, SourceRenderingBehavior, TestBehavior

RAW_DIR = Path("/opt/airflow/data/raw")
SITE_DATA = Path("/opt/airflow/site/data")  # ../site mounted by docker-compose
DBT_DIR = "/opt/airflow/dbt"
DBT_BIN = os.environ.get("DBT_EXECUTABLE", "/home/airflow/dbt_venv/bin/dbt")
TZ = "America/Chicago"
CATALOG = os.environ.get("DATABRICKS_CATALOG") or "bank_health"
JOB_NAME = "bank_health_pipeline"

# Airflow Variables used as watermarks (visible under Admin -> Variables)
FDIC_WATERMARK = "fdic_last_repdte"  # e.g. "2026-06-30"
CFPB_WATERMARK = "cfpb_last_date_received"  # e.g. "2026-10-18"


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@dag(
    dag_id="bank_health_weekly",
    schedule="0 6 * * 1",
    start_date=pendulum.datetime(2026, 10, 1, tz=TZ),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    params={"retrain_model": Param(False, type="boolean", description="Retrain and re-register the model")},
    doc_md=__doc__,
    tags=["bank-health", "fdic", "cfpb", "databricks", "snowflake", "dbt"],
)
def bank_health_weekly():

    @task(
        doc_md="FDIC: new quarters (+ last 2 refreshed), institutions, failures -> data/raw/fdic. "
        "Complete quarters already on disk are reused, so retries are cheap."
    )
    def download_fdic() -> dict:
        from ingestion import fdic

        today = pendulum.now(TZ).date()
        out = RAW_DIR / "fdic"
        last = _as_date(Variable.get(FDIC_WATERMARK, default=None))
        latest = fdic.latest_financials_quarter()
        quarters = fdic.quarters_to_download(last, latest)
        to_fetch, cached = fdic.split_cached_quarters(quarters, out, latest)
        fresh = fdic.download_financials(to_fetch, out)
        fresh.append(fdic.download_institutions(out, today))
        fresh.append(fdic.download_failures(out, today))
        return {
            "fresh": [str(p) for p in fresh],
            "cached": [str(p) for p in cached],
            "watermark": latest.isoformat(),
            "previous": last.isoformat() if last else None,
        }

    @task(
        doc_md="Retrain only when a new FDIC quarter has arrived (about quarterly) or when the run is "
        "triggered with retrain_model=true; other weeks just re-score with @champion. "
        "Text values like 'False'/'false'/'0' count as false."
    )
    def decide_retrain(fdic_result: dict) -> str:
        from airflow.sdk import get_current_context

        from ingestion.pipeline_flags import should_retrain

        requested = get_current_context()["params"].get("retrain_model", False)
        previous = _as_date(fdic_result["previous"])
        latest = _as_date(fdic_result["watermark"])
        retrain = should_retrain(requested, previous, latest)
        print(
            f"retrain_model param={requested!r}, previous quarter={previous}, latest={latest} -> retrain={retrain}"
        )
        return "true" if retrain else "false"

    @task(
        doc_md="CFPB complaints since the watermark (bureaus excluded) -> data/raw/cfpb. "
        "Complete months already on disk are reused."
    )
    def download_cfpb() -> dict:
        from ingestion import cfpb

        today = pendulum.now(TZ).date()
        last = _as_date(Variable.get(CFPB_WATERMARK, default=None))
        result = cfpb.download_complaints(last, today, RAW_DIR / "cfpb")
        return {
            "fresh": [str(p) for p in result["fresh"]],
            "cached": [str(p) for p in result["cached"]],
            "watermark": (today - timedelta(days=1)).isoformat(),
        }

    @task(doc_md="Upload fresh files to the Databricks Volume; reused files only if the Volume lacks them.")
    def upload_raw(fdic_result: dict, cfpb_result: dict) -> dict:
        from ingestion.databricks_files import DatabricksFiles

        dbx = DatabricksFiles.from_env()
        fresh = [Path(p) for p in fdic_result["fresh"] + cfpb_result["fresh"]]
        cached = [Path(p) for p in fdic_result["cached"] + cfpb_result["cached"]]
        uploaded = dbx.upload_files(fresh, RAW_DIR)
        backfilled = dbx.upload_if_missing(cached, RAW_DIR)
        return {
            "fresh_uploaded": len(uploaded),
            "cached_uploaded": len(backfilled),
            "cached_already_in_volume": len(cached) - len(backfilled),
        }

    @task(doc_md="Advance the FDIC and CFPB watermarks once the files are safely in Databricks.")
    def save_watermarks(fdic_result: dict, cfpb_result: dict, upload_result: dict) -> dict:
        Variable.set(FDIC_WATERMARK, fdic_result["watermark"])
        Variable.set(CFPB_WATERMARK, cfpb_result["watermark"])
        return {"fdic": fdic_result["watermark"], "cfpb": cfpb_result["watermark"]}

    @task(doc_md="Pull the Databricks Git folder to latest main and create/update the job definition.")
    def sync_databricks_job() -> int:
        from ingestion import databricks_jobs as dj

        api = dj.DatabricksAPI.from_env()
        repo = dj.pull_git_folder(api)
        return dj.create_or_update_job(api, dj.job_settings(repo["path"], CATALOG))

    run_databricks_job = DatabricksRunNowOperator(
        task_id="run_databricks_job",
        doc_md="Run job bank_health_pipeline (bronze -> silver -> gold -> train if decide_retrain says so -> "
        "score) on serverless and wait. One retry only, to protect the Free Edition daily quota.",
        databricks_conn_id="databricks_default",
        job_name=JOB_NAME,
        job_parameters={"catalog": CATALOG, "retrain": "{{ ti.xcom_pull(task_ids='decide_retrain') }}"},
        wait_for_termination=True,
        polling_period_seconds=60,
        retries=1,
        execution_timeout=timedelta(hours=3),
    )

    @task(doc_md="Load Databricks Parquet exports into Snowflake RAW (atomic swap per table).")
    def load_snowflake() -> dict:
        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

        from ingestion.databricks_files import DatabricksFiles
        from ingestion.snowflake_load import load_all

        conn = SnowflakeHook(snowflake_conn_id="snowflake_default").get_conn()
        try:
            return load_all(conn, DatabricksFiles.from_env())
        finally:
            conn.close()

    @task(
        retries=0,
        doc_md="Release guardrail on RAW.ML_RISK_SCORES, before dbt publishes the marts: the 20 riskiest "
        "scored banks must not include a new bank, a foreign-bank office, a 0% or missing Tier 1 "
        "leverage ratio, or a reason quoting an unusable capital ratio. Fails with the list of problems.",
    )
    def sanity_check_top20() -> dict:
        import logging

        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

        from ingestion import sanity_checks

        logging.getLogger("snowflake").setLevel(logging.ERROR)  # login URL contains the account
        conn = SnowflakeHook(snowflake_conn_id="snowflake_default").get_conn()
        try:
            cur = conn.cursor()
            try:
                return sanity_checks.run(cur, "RAW.ML_RISK_SCORES")
            finally:
                cur.close()
        finally:
            conn.close()

    dbt_build = DbtTaskGroup(
        group_id="dbt_build",
        project_config=ProjectConfig(DBT_DIR),
        profile_config=ProfileConfig(
            profile_name="bank_health", target_name="prod", profiles_yml_filepath=f"{DBT_DIR}/profiles.yml"
        ),
        execution_config=ExecutionConfig(dbt_executable_path=DBT_BIN),
        # dbt packages are installed once into dbt/dbt_packages (`dbt deps`), so neither
        # parsing nor each task needs to download them again.
        render_config=RenderConfig(
            load_method=LoadMode.DBT_LS,
            dbt_deps=False,
            test_behavior=TestBehavior.AFTER_EACH,
            source_rendering_behavior=SourceRenderingBehavior.WITH_TESTS_OR_FRESHNESS,
            should_detach_multiple_parents_tests=True,
        ),
        operator_args={"install_deps": False},
        default_args={"retries": 1},
    )

    dbt_docs_generate = BashOperator(
        task_id="dbt_docs_generate",
        doc_md="Generate the dbt docs site (models, tests, lineage) into dbt/target.",
        bash_command=f"cd {DBT_DIR} && {DBT_BIN} docs generate --profiles-dir . --target-path target",
    )

    @task(doc_md="End-to-end checks in Snowflake: tables populated, fresh and consistent.")
    def verify_pipeline(watermarks: dict) -> dict:
        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

        hook = SnowflakeHook(snowflake_conn_id="snowflake_default")
        tables = [
            "RAW.FDIC_FINANCIALS",
            "RAW.FDIC_INSTITUTIONS",
            "RAW.FDIC_FAILURES",
            "RAW.CFPB_COMPLAINTS",
            "RAW.ML_RISK_SCORES",
            "MARTS.DIM_BANK",
            "MARTS.FCT_BANK_QUARTER",
            "MARTS.FCT_COMPLAINTS",
            "MARTS.MART_BANK_SCORECARD",
            "MARTS.MART_EARLY_WARNING",
        ]
        counts = {t: hook.get_first(f"select count(*) from {t}")[0] for t in tables}
        problems = [t for t, n in counts.items() if not n]
        latest_q = str(hook.get_first("select max(report_date) from MARTS.FCT_BANK_QUARTER")[0])
        if latest_q != watermarks["fdic"]:
            problems.append(f"fct_bank_quarter latest {latest_q} != FDIC watermark {watermarks['fdic']}")
        hours = hook.get_first(
            "select datediff(hour, min(_loaded_at), current_timestamp()) from RAW.CFPB_COMPLAINTS"
        )[0]
        if hours > 24:
            problems.append(f"RAW.CFPB_COMPLAINTS loaded {hours}h ago")
        if counts["RAW.CFPB_COMPLAINTS"] != counts["MARTS.FCT_COMPLAINTS"]:
            problems.append("fct_complaints row count differs from RAW")
        if counts["RAW.ML_RISK_SCORES"] != counts["MARTS.MART_EARLY_WARNING"]:
            problems.append("mart_early_warning row count differs from RAW risk scores")
        print("row counts:", counts)
        if problems:
            raise ValueError("Pipeline checks failed: " + "; ".join(problems))
        return {"counts": counts, "latest_quarter": latest_q}

    @task(
        doc_md="Refresh site/data/watchlist.json (one record per scored bank) for the watchlist page. "
        "Overwrites the file atomically, so it is safe to re-run. Commit the file to publish it."
    )
    def export_watchlist() -> dict:
        import logging

        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

        from ingestion.watchlist_export import export

        logging.getLogger("snowflake").setLevel(logging.ERROR)  # login URL contains the account
        conn = SnowflakeHook(snowflake_conn_id="snowflake_default").get_conn()
        try:
            return export(conn, SITE_DATA / "watchlist.json")
        finally:
            conn.close()

    fdic_result = download_fdic()
    cfpb_result = download_cfpb()
    uploads = upload_raw(fdic_result, cfpb_result)
    watermarks = save_watermarks(fdic_result, cfpb_result, uploads)
    retrain = decide_retrain(fdic_result)
    job = sync_databricks_job()
    [watermarks, job, retrain] >> run_databricks_job
    loaded = load_snowflake()
    run_databricks_job >> loaded >> sanity_check_top20() >> dbt_build >> dbt_docs_generate
    dbt_docs_generate >> verify_pipeline(watermarks) >> export_watchlist()


bank_health_weekly()
