"""Smoke tests: is the local Airflow stack up, healthy and able to load the DAG?

Run from the project folder on your PC, with the stack running
(`docker compose up -d` in airflow/):

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
COMPOSE = ["docker", "compose", "-f", str(ROOT / "airflow" / "docker-compose.yaml")]
BASE_URL = "http://localhost:8080"
EXPECTED_SERVICES = {"postgres", "airflow-apiserver", "airflow-scheduler", "airflow-dag-processor"}


def compose(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        [*COMPOSE, *args], input=stdin, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, f"docker compose {' '.join(args)} failed:\n{result.stderr[-2000:]}"
    return result.stdout


# ---------------------------------------------------------------- containers
def test_all_containers_running_and_healthy():
    rows = [json.loads(line) for line in compose("ps", "--format", "json").splitlines() if line.strip()]
    status = {r["Service"]: (r["State"], r.get("Health", "")) for r in rows}
    missing = EXPECTED_SERVICES - status.keys()
    assert not missing, f"containers not running: {sorted(missing)}"
    unhealthy = {s: status[s] for s in EXPECTED_SERVICES if status[s] != ("running", "healthy")}
    assert not unhealthy, f"unhealthy containers: {unhealthy}"


# ------------------------------------------------------------------- web/API
def test_health_endpoint_reports_all_components_healthy():
    resp = requests.get(f"{BASE_URL}/api/v2/monitor/health", timeout=15)
    assert resp.status_code == 200
    body = resp.json()
    for component in ("metadatabase", "scheduler", "dag_processor"):
        assert body[component]["status"] == "healthy", f"{component}: {body[component]}"


def test_ui_page_loads():
    resp = requests.get(BASE_URL + "/", timeout=15)
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


def test_dag_schedule_and_settings(dag_facts):
    dag = dag_facts["bank_health_weekly"]
    assert dag["timetable"]["expression"] == "0 6 * * 1"         # Mondays 06:00
    assert dag["timetable"]["timezone"] == "America/Chicago"
    assert dag["catchup"] is False
    assert dag["max_active_runs"] == 1
    assert dag["has_doc_md"]


def test_dag_tasks_retries_docs_and_order(dag_facts):
    tasks = dag_facts["bank_health_weekly"]["tasks"]
    assert set(tasks) == {"download_fdic", "download_cfpb", "upload_raw", "save_watermarks"}
    for task_id, t in tasks.items():
        assert t["retries"] == 2, task_id
        assert t["has_doc_md"], f"{task_id} has no task docs"
    assert tasks["download_fdic"]["upstream"] == []
    assert tasks["download_cfpb"]["upstream"] == []
    assert tasks["upload_raw"]["upstream"] == ["download_cfpb", "download_fdic"]
    # watermarks only move after the upload succeeded
    assert "upload_raw" in tasks["save_watermarks"]["upstream"]
