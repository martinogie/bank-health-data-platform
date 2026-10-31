"""Smoke tests: is the local Airflow stack up, healthy and able to load the DAG?

Run from the project folder, with the stack running (`docker compose up -d` in airflow/).
They test the stack that belongs to this folder: plain `docker compose` in airflow/ means prod
in the main checkout and dev in a dev checkout (whose airflow/.env selects
docker-compose.dev.yaml). The UI port is read from the running container (prod 8080, dev 8081).

    .venv\\Scripts\\python -m pytest tests/smoke -m smoke

They talk to the running containers, so they are not part of the default
offline `pytest` run (pytest.ini testpaths = ingestion/tests).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.smoke

ROOT = Path(__file__).resolve().parents[2]
AIRFLOW_DIR = ROOT / "airflow"
EXPECTED_SERVICES = {"postgres", "airflow-apiserver", "airflow-scheduler", "airflow-dag-processor"}


def compose(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["docker", "compose", *args],
        cwd=AIRFLOW_DIR,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"docker compose {' '.join(args)} failed:\n{result.stderr[-2000:]}"
    return result.stdout


@pytest.fixture(scope="module")
def base_url() -> str:
    """http://localhost:<port> of this stack's API server (docker compose port)."""
    host_port = compose("port", "airflow-apiserver", "8080").strip().rsplit(":", 1)[1]
    return f"http://localhost:{host_port}"


# ---------------------------------------------------------------- containers
def test_all_containers_running_and_healthy():
    rows = [json.loads(line) for line in compose("ps", "--format", "json").splitlines() if line.strip()]
    status = {r["Service"]: (r["State"], r.get("Health", "")) for r in rows}
    missing = EXPECTED_SERVICES - status.keys()
    assert not missing, f"containers not running: {sorted(missing)}"
    unhealthy = {s: status[s] for s in EXPECTED_SERVICES if status[s] != ("running", "healthy")}
    assert not unhealthy, f"unhealthy containers: {unhealthy}"


# ------------------------------------------------------------------- web/API
def test_health_endpoint_reports_all_components_healthy(base_url):
    resp = requests.get(f"{base_url}/api/v2/monitor/health", timeout=15)
    assert resp.status_code == 200
    body = resp.json()
    for component in ("metadatabase", "scheduler", "dag_processor"):
        assert body[component]["status"] == "healthy", f"{component}: {body[component]}"


def test_ui_page_loads(base_url):
    resp = requests.get(base_url + "/", timeout=15)
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()


# ----------------------------------------------------------------------- DAG
@pytest.fixture(scope="module")
def dag_facts() -> dict:
    """Parse dags/ inside the scheduler container and return the facts as a dict."""
    script = (Path(__file__).parent / "dag_facts.py").read_text(encoding="utf-8")
    out = compose("exec", "-T", "airflow-scheduler", "python", "-", stdin=script)
    line = next(l for l in out.splitlines() if l.startswith("DAG_FACTS_JSON="))
    return json.loads(line.split("=", 1)[1])


def test_dags_import_without_errors(dag_facts):
    assert dag_facts["import_errors"] == {}
    assert "bank_health_weekly" in dag_facts["dag_ids"]
    assert "bank_health_slo_check" in dag_facts["dag_ids"]


def test_dag_schedule_and_settings(dag_facts):
    dag = dag_facts["bank_health_weekly"]
    assert dag["timetable"]["expression"] == "0 6 * * 1"  # Mondays 06:00
    assert dag["timetable"]["timezone"] == "America/Chicago"
    assert dag["catchup"] is False
    assert dag["max_active_runs"] == 1
    assert dag["has_doc_md"]


CORE_TASKS = {
    "download_fdic",
    "download_cfpb",
    "upload_raw",
    "save_watermarks",
    "decide_retrain",
    "sync_databricks_job",
    "run_databricks_job",
    "load_snowflake",
    "dbt_docs_generate",
    "verify_pipeline",
    "export_watchlist",
}


def test_dag_tasks_retries_docs_and_order(dag_facts):
    tasks = dag_facts["bank_health_weekly"]["tasks"]
    assert CORE_TASKS <= set(tasks)
    for task_id in CORE_TASKS:
        expected = 1 if task_id == "run_databricks_job" else 2  # 1 retry protects the Databricks quota
        assert tasks[task_id]["retries"] == expected, task_id
        assert tasks[task_id]["has_doc_md"], f"{task_id} has no task docs"
    # every run starts by logging and checking the environment (read-only, no retries)
    assert tasks["check_environment"]["upstream"] == []
    assert tasks["check_environment"]["retries"] == 0 and tasks["check_environment"]["has_doc_md"]
    assert tasks["download_fdic"]["upstream"] == ["check_environment"]
    assert tasks["download_cfpb"]["upstream"] == ["check_environment"]
    assert tasks["sync_databricks_job"]["upstream"] == ["check_environment"]
    # data contracts are checked before anything is uploaded (no retries: a broken contract stays broken)
    assert tasks["check_contracts"]["upstream"] == ["download_cfpb", "download_fdic"]
    assert tasks["check_contracts"]["retries"] == 0 and tasks["check_contracts"]["has_doc_md"]
    assert tasks["upload_raw"]["upstream"] == ["check_contracts", "download_cfpb", "download_fdic"]
    # watermarks only move after the upload succeeded
    assert "upload_raw" in tasks["save_watermarks"]["upstream"]
    assert {"save_watermarks", "sync_databricks_job", "decide_retrain"} <= set(
        tasks["run_databricks_job"]["upstream"]
    )
    assert tasks["decide_retrain"]["upstream"] == ["download_fdic"]
    assert tasks["load_snowflake"]["upstream"] == ["run_databricks_job"]
    # release guardrail: no retries (a bad top 20 won't fix itself), runs before dbt publishes marts
    assert tasks["sanity_check_top20"]["upstream"] == ["load_snowflake"]
    assert tasks["sanity_check_top20"]["retries"] == 0 and tasks["sanity_check_top20"]["has_doc_md"]
    # Phase 2: metrics, then freshness and volume gates before the watchlist; record_run last
    assert tasks["collect_metrics"]["upstream"] == ["verify_pipeline"]
    assert tasks["export_watchlist"]["upstream"] == ["check_freshness", "check_volumes"]
    assert tasks["record_run"]["upstream"] == ["export_watchlist", "publish_dbt_manifest"]
    assert "all_done" in tasks["record_run"]["trigger_rule"]  # runs even after failures
    assert all(t["has_failure_alert"] for t in tasks.values()), "every task must alert on failure"
    # prod-only publish of the dbt manifest for slim CI, after the docs (full manifest) are built
    assert tasks["publish_dbt_manifest"]["upstream"] == ["dbt_docs_generate"]


def test_cosmos_renders_dbt_models_as_tasks(dag_facts):
    tasks = dag_facts["bank_health_weekly"]["tasks"]
    dbt_tasks = [t for t in tasks if t.startswith("dbt_build.")]
    for model in ("stg_fdic__financials", "fct_bank_quarter", "mart_early_warning"):
        assert any(model in t for t in dbt_tasks), f"no Cosmos task for {model}"
    assert all(tasks[t]["retries"] == 1 for t in dbt_tasks)
    assert any(
        t.endswith("sanity_check_top20") for t in (u for t in dbt_tasks for u in tasks[t]["upstream"])
    ), "dbt group must run after the top-20 check"
