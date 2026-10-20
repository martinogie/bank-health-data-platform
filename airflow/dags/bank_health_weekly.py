"""
### Bank Health — weekly pipeline

Every Monday 06:00 America/Chicago. Every task is safe to re-run (clear it, or the whole run):

| Task | What it does | Why a re-run is safe |
|---|---|---|
| `download_fdic` | new FDIC quarters (last 2 re-pulled for amendments), institutions, failures | fixed file names; complete quarters already on disk are reused |
| `download_cfpb` | complaints since the watermark (30-day look-back), credit bureaus excluded | complete months already on disk are reused |
| `upload_raw` | fresh files -> Databricks Volume; reused files only if the Volume lacks them | uploads overwrite |
| `save_watermarks` | remember how far we got (after the upload succeeded) | writes the same values again |
| `sync_databricks_job` | pull the Git folder, create/update job `bank_health_pipeline` | both idempotent |
| `run_databricks_job` | bronze -> silver -> gold -> train (only if `retrain_model`) -> score | Auto Loader checkpoints; silver/gold/scores overwrite; job allows 1 run at a time |
| `load_snowflake` | Databricks exports -> Snowflake `RAW` | each table replaced by an atomic swap |
| `dbt_build` | Cosmos: one task per dbt model with its tests, plus source freshness | dbt rebuilds models |
| `dbt_docs_generate` | dbt docs site (for GitHub Pages) | regenerates files |
| `verify_pipeline` | end-to-end checks in Snowflake | read-only |

Trigger with `{"retrain_model": true}` to retrain and re-register the model.
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
DBT_DIR = "/opt/airflow/dbt"
DBT_BIN = os.environ.get("DBT_EXECUTABLE", "/home/airflow/dbt_venv/bin/dbt")
TZ = "America/Chicago"
CATALOG = os.environ.get("DATABRICKS_CATALOG") or "bank_health"
JOB_NAME = "bank_health_pipeline"

# Airflow Variables used as watermarks (visible under Admin -> Variables)
FDIC_WATERMARK = "fdic_last_repdte"           # e.g. "2026-06-30"
CFPB_WATERMARK = "cfpb_last_date_received"    # e.g. "2026-10-18"


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

    @task(doc_md="FDIC: new quarters (+ last 2 refreshed), institutions, failures -> data/raw/fdic. "
                 "Complete quarters already on disk are reused, so retries are cheap.")
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
        return {"fresh": [str(p) for p in fresh], "cached": [str(p) for p in cached],
                "watermark": latest.isoformat()}

    @task(doc_md="CFPB complaints since the watermark (bureaus excluded) -> data/raw/cfpb. "
                 "Complete months already on disk are reused.")
    def download_cfpb() -> dict:
        from ingestion import cfpb

        today = pendulum.now(TZ).date()
        last = _as_date(Variable.get(CFPB_WATERMARK, default=None))
        result = cfpb.download_complaints(last, today, RAW_DIR / "cfpb")
        return {"fresh": [str(p) for p in result["fresh"]], "cached": [str(p) for p in result["cached"]],
                "watermark": (today - timedelta(days=1)).isoformat()}

    @task(doc_md="Upload fresh files to the Databricks Volume; reused files only if the Volume lacks them.")
    def upload_raw(fdic_result: dict, cfpb_result: dict) -> dict:
        from ingestion.databricks_files import DatabricksFiles

        dbx = DatabricksFiles.from_env()
        fresh = [Path(p) for p in fdic_result["fresh"] + cfpb_result["fresh"]]
        cached = [Path(p) for p in fdic_result["cached"] + cfpb_result["cached"]]
        uploaded = dbx.upload_files(fresh, RAW_DIR)
        backfilled = dbx.upload_if_missing(cached, RAW_DIR)
        return {"fresh_uploaded": len(uploaded), "cached_uploaded": len(backfilled),
                "cached_already_in_volume": len(cached) - len(backfilled)}

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
        doc_md="Run job bank_health_pipeline (bronze -> silver -> gold -> train if retrain_model -> score) "
               "on serverless and wait. One retry only, to protect the Free Edition daily quota.",
        databricks_conn_id="databricks_default",
        job_name=JOB_NAME,
        job_parameters={"catalog": CATALOG,
                        "retrain": "{{ 'true' if params.retrain_model else 'false' }}"},
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

    dbt_build = DbtTaskGroup(
        group_id="dbt_build",
        project_config=ProjectConfig(DBT_DIR),
        profile_config=ProfileConfig(profile_name="bank_health", target_name="prod",
                                     profiles_yml_filepath=f"{DBT_DIR}/profiles.yml"),
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
        tables = ["RAW.FDIC_FINANCIALS", "RAW.FDIC_INSTITUTIONS", "RAW.FDIC_FAILURES",
                  "RAW.CFPB_COMPLAINTS", "RAW.ML_RISK_SCORES", "MARTS.DIM_BANK",
                  "MARTS.FCT_BANK_QUARTER", "MARTS.FCT_COMPLAINTS", "MARTS.MART_BANK_SCORECARD",
                  "MARTS.MART_EARLY_WARNING"]
        counts = {t: hook.get_first(f"select count(*) from {t}")[0] for t in tables}
        problems = [t for t, n in counts.items() if not n]
        latest_q = str(hook.get_first("select max(report_date) from MARTS.FCT_BANK_QUARTER")[0])
        if latest_q != watermarks["fdic"]:
            problems.append(f"fct_bank_quarter latest {latest_q} != FDIC watermark {watermarks['fdic']}")
        hours = hook.get_first("select datediff(hour, min(_loaded_at), current_timestamp()) from RAW.CFPB_COMPLAINTS")[0]
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

    fdic_result = download_fdic()
    cfpb_result = download_cfpb()
    uploads = upload_raw(fdic_result, cfpb_result)
    watermarks = save_watermarks(fdic_result, cfpb_result, uploads)
    job = sync_databricks_job()
    [watermarks, job] >> run_databricks_job
    loaded = load_snowflake()
    run_databricks_job >> loaded >> dbt_build >> dbt_docs_generate >> verify_pipeline(watermarks)


bank_health_weekly()
