"""
### Bank Health — weekly pipeline

Every Monday 06:00 America/Chicago. Every task is safe to re-run (clear it, or the whole run).

**Environment:** `BANK_HEALTH_ENV` (dev or prod, set in the Compose file) decides the Snowflake
database, Databricks catalog and job, and dbt target. It is required: if it is missing or wrong,
this DAG fails to load. Only prod publishes the watchlist and the dbt manifest.

| Task | What it does | Why a re-run is safe |
|---|---|---|
| `check_environment` | logs the environment; checks the Snowflake session is in that environment's database; in prod, stops unless this machine is the one named in `OPS.PROD_SCHEDULER` (one scheduler only) | read-only |
| `download_fdic` | new FDIC quarters (last 2 re-pulled for amendments), institutions, failures | fixed file names; complete quarters already on disk are reused |
| `download_cfpb` | complaints since the watermark (30-day look-back), credit bureaus excluded | complete months already on disk are reused |
| `upload_raw` | fresh files -> Databricks Volume; reused files only if the Volume lacks them | uploads overwrite |
| `save_watermarks` | remember how far we got (after the upload succeeded) | writes the same values again |
| `decide_retrain` | retrain only if a new FDIC quarter arrived or `retrain_model` is true | pure decision |
| `sync_databricks_job` | prod: pull the Git folder to main; dev: upload this checkout's notebooks. Create/update the job | both idempotent |
| `run_databricks_job` | bronze -> silver -> gold -> train (only if `decide_retrain` says so) -> score | Auto Loader checkpoints; silver/gold/scores overwrite; job allows 1 run at a time |
| `load_snowflake` | Databricks exports -> Snowflake `RAW` | each table replaced by an atomic swap |
| `sanity_check_top20` | fails if the 20 riskiest scored banks include a new bank, a foreign-bank office or a 0% capital ratio | read-only |
| `dbt_build` | Cosmos: one task per dbt model with its tests, plus source freshness | dbt rebuilds models |
| `dbt_docs_generate` | dbt docs site (for GitHub Pages) | regenerates files |
| `publish_dbt_manifest` | prod only: upload `manifest.json` to `@BANK_HEALTH.RAW.DBT_ARTIFACTS` for dbt slim CI | overwrites the file |
| `verify_pipeline` | end-to-end checks in Snowflake | read-only |
| `collect_metrics` | row counts per layer (Databricks notebook summaries, Snowflake MARTS), latest FDIC quarter | read-only |
| `check_freshness` | MARTS rebuilt in this run, the FDIC quarter expected for today, scored banks in range | read-only |
| `check_volumes` | row counts vs the median of the last 4 successful runs (thresholds in `ingestion/volume_checks.py`) | read-only |
| `export_watchlist` | prod only: refresh `watchlist.json` in `WATCHLIST_DIR` (outside the git checkout) for the watchlist page | file replaced atomically |
| `record_run` | always runs last (even after failures): one `OPS.PIPELINE_RUNS` row; the weekly success message | replaces this run's row |

**Alerts** (Slack and Discord, `ingestion/alerts.py`): any task failing after its last retry, the
freshness and volume checks, a run not finished by Monday 12:00 CT (`bank_health_slo_check`), and
one success message per run. Response guide: `docs/runbooks/alerts.md`.

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

from ingestion import environment
from ingestion.alerts import task_failure_callback

# Fails the DAG import (shown in the Airflow UI) if BANK_HEALTH_ENV is missing or invalid, or if
# .env belongs to another environment. Nothing runs with a guessed environment.
ENV = environment.settings()
if ENV.databricks_job_name is None or ENV.databricks_catalog is None:
    raise environment.EnvironmentMismatch(
        f"BANK_HEALTH_ENV={ENV.name}: the pipeline DAG runs in dev or prod, not {ENV.name}"
    )

RAW_DIR = Path("/opt/airflow/data/raw")
SITE_DATA = Path("/opt/airflow/site/data")  # WATCHLIST_DIR on the host (docker-compose.yaml)
DBT_DIR = "/opt/airflow/dbt"
DBT_BIN = os.environ.get("DBT_EXECUTABLE", "/home/airflow/dbt_venv/bin/dbt")
TZ = "America/Chicago"
CATALOG = ENV.databricks_catalog
JOB_NAME = ENV.databricks_job_name

# Airflow Variables used as watermarks (visible under Admin -> Variables)
FDIC_WATERMARK = "fdic_last_repdte"  # e.g. "2026-06-30"
CFPB_WATERMARK = "cfpb_last_date_received"  # e.g. "2026-10-18"


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


# ------------------------------------------------------------------ helpers for the Phase 2 tasks
class _snowflake_cursor:
    """`with _snowflake_cursor() as cur:` (the account is in the login URL, so connector logs are quiet)."""

    def __enter__(self):
        import logging

        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

        logging.getLogger("snowflake").setLevel(logging.ERROR)
        self.conn = SnowflakeHook(snowflake_conn_id="snowflake_default").get_conn()
        return self.conn.cursor()

    def __exit__(self, *exc):
        self.conn.close()
        return False


def _collect_metrics(ti) -> dict:
    """Current counts, JSON-safe. Each source is optional: a failed run has fewer of them."""
    from ingestion import run_log
    from ingestion.databricks_jobs import DatabricksAPI

    out: dict = {"layers": {}, "marts": None, "latest_quarter": None}
    run_id = ti.xcom_pull(task_ids="run_databricks_job", key="run_id")
    if run_id:
        try:
            out["layers"] = run_log.layer_rows(run_log.databricks_outputs(DatabricksAPI.from_env(), run_id))
        except Exception as exc:
            print(f"Databricks summaries not available: {type(exc).__name__}")
    try:
        with _snowflake_cursor() as cur:
            out["marts"] = run_log.marts_counts(cur)
            out["latest_quarter"] = str(run_log.latest_fdic_quarter(cur))
    except Exception as exc:
        print(f"Snowflake counts not available: {type(exc).__name__}")
    layers, marts = out["layers"], out["marts"] or {}
    out["current"] = {
        "BRONZE_ROWS": layers.get("BRONZE_ROWS"),
        "SILVER_ROWS": layers.get("SILVER_ROWS"),
        "GOLD_ROWS": layers.get("GOLD_ROWS"),
        "MARTS_ROWS": sum(marts.values()) if marts else None,
        "SCORED_BANKS": marts.get("MART_EARLY_WARNING"),
    }
    print("metrics:", out["current"])
    return out


def _task_states(dag_id: str, run_id: str) -> dict[str, str | None]:
    """{task_id: state} for this run, through the Task SDK (tasks can't read the metadata DB)."""
    from airflow.sdk.execution_time.task_runner import RuntimeTaskInstance

    states = RuntimeTaskInstance.get_task_states(dag_id=dag_id, run_ids=[run_id])
    inner = states.get(run_id, states) if isinstance(states, dict) else {}
    return {str(k): (str(v) if v is not None else None) for k, v in inner.items()}


def _record_run() -> dict:
    from airflow.sdk import get_current_context

    from ingestion import run_log, scheduler_guard
    from ingestion.alerts import notify

    ctx = get_current_context()
    ti, dag_run = ctx["ti"], ctx["dag_run"]
    host = os.environ.get("PROD_HOST_ID") or None
    with _snowflake_cursor() as cur:
        if ENV.is_prod:
            try:
                scheduler_guard.check(cur, host)
            except scheduler_guard.SchedulerNotAuthorized as exc:
                print(f"Not the prod scheduler, nothing recorded: {exc}")
                return {"recorded": False}
        state, failed = run_log.run_outcome(_task_states(dag_run.dag_id, dag_run.run_id), ti.task_id)
        metrics = ti.xcom_pull(task_ids="collect_metrics") or _collect_metrics(ti)
        volume = ti.xcom_pull(task_ids="check_volumes", key="volume_result") or {}
        ended = pendulum.now("UTC")
        warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE", "")
        try:
            dbt = run_log.dbt_counts(cur, dag_run.run_id)
        except Exception as exc:
            print(f"dbt results not available: {type(exc).__name__}")
            dbt = {}
        credits = (
            run_log.credits_used(cur, warehouse, dag_run.start_date, ended) if dag_run.start_date else None
        )
        row = run_log.build_row(
            run_id=dag_run.run_id,
            environment=ENV.name,
            host_id=host or (None if ENV.is_prod else "dev"),
            dag_id=dag_run.dag_id,
            started_at=dag_run.start_date or ended,  # STARTED_AT is NOT NULL
            ended_at=ended,
            state=state,
            failed_tasks=failed,
            layers=metrics.get("layers"),
            marts=metrics.get("marts"),
            dbt=dbt,
            credits=credits,
            latest_quarter=metrics.get("latest_quarter"),
            volume_anomalies=volume.get("anomalies"),
        )
        run_log.write_row(cur, row)
        backfilled = run_log.backfill_final_credits(cur, warehouse, ended)
    print(
        f"recorded {dag_run.run_id}: {state}, failed {failed or 'none'}; final credits filled for {backfilled} run(s)"
    )
    if state == "success":
        notify(*run_log.success_message(row))
    return {"recorded": True, "state": state, "failed_tasks": failed}


@dag(
    dag_id="bank_health_weekly",
    schedule="0 6 * * 1",
    start_date=pendulum.datetime(2026, 10, 1, tz=TZ),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": task_failure_callback,  # alert once, after the last retry
    },
    params={"retrain_model": Param(False, type="boolean", description="Retrain and re-register the model")},
    doc_md=__doc__,
    tags=["bank-health", ENV.name, "fdic", "cfpb", "databricks", "snowflake", "dbt"],
)
def bank_health_weekly():
    @task(
        retries=0,
        doc_md="First task of every run: log the environment (names only) and check that the "
        "Snowflake connection is in this environment's database. Read-only.",
    )
    def check_environment() -> dict:
        import logging

        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

        from ingestion.snowflake_load import check_target_database

        print("BANK_HEALTH_ENV:", environment.describe())
        logging.getLogger("snowflake").setLevel(logging.ERROR)  # login URL contains the account
        conn = SnowflakeHook(snowflake_conn_id="snowflake_default").get_conn()
        try:
            database = check_target_database(conn)
        finally:
            conn.close()
        print(f"Snowflake session database: {database}")
        host = os.environ.get("PROD_HOST_ID") or None
        if ENV.is_prod:
            # One scheduler only: stop here unless OPS.PROD_SCHEDULER names this machine.
            from ingestion import scheduler_guard

            with _snowflake_cursor() as cur:
                host = scheduler_guard.check(cur, host)
            print(f"Prod scheduler: this machine ({host}) is the one assigned in {scheduler_guard.TABLE}")
        return {"env": ENV.name, "snowflake_database": database, "databricks_catalog": CATALOG, "host": host}

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
        latest = date.fromisoformat(fdic_result["watermark"])  # always set by download_fdic
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
        return dj.create_or_update_job(api, dj.job_settings(dj.sync_notebooks(api, ENV), ENV))

    run_databricks_job = DatabricksRunNowOperator(
        task_id="run_databricks_job",
        doc_md="Run job bank_health_pipeline (bronze -> silver -> gold -> train if decide_retrain says so -> "
        "score) on serverless and wait. One retry only, to protect the Free Edition daily quota.",
        databricks_conn_id="databricks_default",
        job_name=JOB_NAME,
        job_parameters={
            "catalog": CATALOG,
            "env": ENV.name,
            "retrain": "{{ ti.xcom_pull(task_ids='decide_retrain') }}",
        },
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
            profile_name="bank_health",
            target_name=ENV.dbt_target,
            profiles_yml_filepath=f"{DBT_DIR}/profiles.yml",
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
        # AIRFLOW_RUN_ID tags every dbt result in OPS.DBT_RESULTS (macros/record_dbt_results.sql).
        operator_args={"install_deps": False, "env": {"AIRFLOW_RUN_ID": "{{ run_id }}"}, "append_env": True},
        default_args={"retries": 1, "on_failure_callback": task_failure_callback},
    )

    dbt_docs_generate = BashOperator(
        task_id="dbt_docs_generate",
        doc_md="Generate the dbt docs site (models, tests, lineage) into dbt/target.",
        bash_command=f"cd {DBT_DIR} && {DBT_BIN} docs generate --profiles-dir . --target {ENV.dbt_target} "
        "--target-path target",
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
        doc_md="Refresh watchlist.json (one record per scored bank) for the watchlist page, in "
        "WATCHLIST_DIR outside the git checkout. Overwrites the file atomically, so it is safe to re-run. "
        "To publish, copy it to site/data/ on a branch and open a pull request."
    )
    def export_watchlist() -> dict:
        import logging

        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook
        from airflow.sdk.exceptions import AirflowSkipException

        from ingestion.watchlist_export import export

        if not ENV.is_prod:
            raise AirflowSkipException(f"BANK_HEALTH_ENV={ENV.name}: only prod publishes the watchlist")
        logging.getLogger("snowflake").setLevel(logging.ERROR)  # login URL contains the account
        conn = SnowflakeHook(snowflake_conn_id="snowflake_default").get_conn()
        try:
            return export(conn, SITE_DATA / "watchlist.json")
        finally:
            conn.close()

    @task(
        doc_md="Prod only: upload dbt/target/manifest.json (from dbt_docs_generate, after a successful "
        "dbt build) to @BANK_HEALTH.RAW.DBT_ARTIFACTS. dbt slim CI compares pull requests against it. "
        "Skipped in dev."
    )
    def publish_dbt_manifest() -> dict:
        from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook
        from airflow.sdk.exceptions import AirflowSkipException

        from ingestion.dbt_manifest import publish_manifest

        if not ENV.is_prod:
            raise AirflowSkipException(f"BANK_HEALTH_ENV={ENV.name}: only prod publishes the dbt manifest")
        conn = SnowflakeHook(snowflake_conn_id="snowflake_default").get_conn()
        try:
            return publish_manifest(conn, Path(DBT_DIR) / "target" / "manifest.json")
        finally:
            conn.close()

    @task(
        retries=1,
        doc_md="Row counts per layer for the volume checks and the run log: bronze, silver and gold from "
        "the Databricks notebooks' exit summaries, MARTS from Snowflake. Read-only.",
    )
    def collect_metrics() -> dict:
        from airflow.sdk import get_current_context

        return _collect_metrics(get_current_context()["ti"])

    @task(
        retries=0,
        doc_md="Freshness gate before the watchlist: every MARTS table rebuilt in this run, the FDIC quarter "
        "expected for today's date, scored banks in range (ingestion/freshness.py). Fails (and alerts) if not.",
    )
    def check_freshness(metrics: dict) -> dict:
        from airflow.sdk import get_current_context

        from ingestion import freshness, volume_checks

        ctx = get_current_context()
        with _snowflake_cursor() as cur:
            history = volume_checks.fetch_history(cur, ctx["dag_run"].run_id)
            median = volume_checks.baseline(history).get("SCORED_BANKS")
            result = freshness.check(cur, ctx["dag_run"].start_date, pendulum.now(TZ).date(), median)
        print(result)
        if result["problems"]:
            raise ValueError("Freshness check failed: " + "; ".join(result["problems"]))
        return result

    @task(
        retries=0,
        doc_md="Row counts vs the median of the last 4 successful runs in OPS.PIPELINE_RUNS. Fails (and "
        "alerts) on a drop or jump beyond the thresholds in ingestion/volume_checks.py; passes with a note "
        "while there are fewer than 3 earlier runs.",
    )
    def check_volumes(metrics: dict) -> dict:
        from airflow.sdk import get_current_context

        from ingestion import volume_checks

        ctx = get_current_context()
        with _snowflake_cursor() as cur:
            history = volume_checks.fetch_history(cur, ctx["dag_run"].run_id)
        result = volume_checks.compare(metrics["current"], history)
        ctx["ti"].xcom_push(key="volume_result", value=result)  # record_run keeps it even on failure
        print(result["note"])
        if result["anomalies"]:
            raise ValueError(
                "Volume anomaly: " + "; ".join(volume_checks.describe(a) for a in result["anomalies"])
            )
        return result

    @task(
        trigger_rule="all_done",
        retries=1,
        doc_md="Always runs last, even after failures: writes this run's OPS.PIPELINE_RUNS row (state, failed "
        "tasks, rows per layer, dbt results, credits, model, latest quarter, SLO) and sends the weekly success "
        "message. On a machine that is not the assigned prod scheduler it writes nothing.",
    )
    def record_run() -> dict:
        return _record_run()

    env_checked = check_environment()
    fdic_result = download_fdic()
    cfpb_result = download_cfpb()
    uploads = upload_raw(fdic_result, cfpb_result)
    watermarks = save_watermarks(fdic_result, cfpb_result, uploads)
    retrain = decide_retrain(fdic_result)
    job = sync_databricks_job()
    env_checked >> [fdic_result, cfpb_result, job]
    [watermarks, job, retrain] >> run_databricks_job
    loaded = load_snowflake()
    run_databricks_job >> loaded >> sanity_check_top20() >> dbt_build >> dbt_docs_generate
    manifest = publish_dbt_manifest()
    dbt_docs_generate >> manifest
    metrics = collect_metrics()
    dbt_docs_generate >> verify_pipeline(watermarks) >> metrics
    watchlist = export_watchlist()
    [check_freshness(metrics), check_volumes(metrics)] >> watchlist
    [watchlist, manifest] >> record_run()


bank_health_weekly()
