"""Databricks job definition + helpers: pull the Git folder, create/update the job.

The job runs the notebooks in order on serverless compute:
    bronze -> silver -> gold -> train_model -> score
Its settings live here (versioned with the repo) and `create_or_update_job` is
idempotent: an existing job with the same name is reset to these settings.

`retrain` defaults to "false": weekly runs score with the current @champion model;
04_train_model only retrains when run with retrain=true.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)

JOB_NAME = "bank_health_pipeline"
REPO_NAME = "bank-health-data-platform"
NOTEBOOK_TASKS = [
    # (task_key, notebook, timeout seconds)
    ("bronze", "01_bronze", 3600),
    ("silver", "02_silver", 3600),
    ("gold", "03_gold", 3600),
    ("train_model", "04_train_model", 3600),
    ("score", "05_score", 1800),
]


class DatabricksAPI:
    def __init__(self, host: str, token: str, session: requests.Session | None = None):
        if not host or not token:
            raise ValueError("Databricks host and token are required (see .env)")
        self.host = host.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    @classmethod
    def from_env(cls) -> "DatabricksAPI":
        return cls(os.environ.get("DATABRICKS_HOST", ""), os.environ.get("DATABRICKS_TOKEN", ""))

    def __repr__(self) -> str:
        return f"DatabricksAPI(host={self.host!r})"

    def call(self, method: str, path: str, **kwargs) -> dict:
        resp = self.session.request(method, self.host + path, timeout=60, **kwargs)
        if resp.status_code >= 400:
            raise requests.HTTPError(
                f"Databricks {method} {path} failed: {resp.status_code} {resp.text[:300]}", response=resp)
        return resp.json() if resp.text else {}


def find_git_folder(api: DatabricksAPI) -> dict:
    """The project's Git folder. The Repos list can be empty on Free Edition even for a
    real Git folder, so fall back to the home-folder path and fetch it by id."""
    repos = api.call("GET", "/api/2.0/repos").get("repos", [])
    for r in repos:
        if r.get("url", "").rstrip("/").removesuffix(".git").endswith(REPO_NAME):
            return r
    me = api.call("GET", "/api/2.0/preview/scim/v2/Me")["userName"]
    status = api.call("GET", "/api/2.0/workspace/get-status", params={"path": f"/Users/{me}/{REPO_NAME}"})
    return api.call("GET", f"/api/2.0/repos/{status['resource_id']}")


def pull_git_folder(api: DatabricksAPI, branch: str = "main") -> dict:
    """Update the Git folder to the latest commit of `branch` (safe to repeat)."""
    repo = find_git_folder(api)
    api.call("PATCH", f"/api/2.0/repos/{repo['id']}", json={"branch": branch})
    repo = api.call("GET", f"/api/2.0/repos/{repo['id']}")
    log.info("Git folder %s at %s @ %s", repo["path"], branch, repo.get("head_commit_id", "")[:7])
    return repo


def job_settings(repo_path: str, catalog: str = "bank_health") -> dict:
    tasks, previous = [], None
    for key, notebook, timeout in NOTEBOOK_TASKS:
        task = {
            "task_key": key,
            "notebook_task": {"notebook_path": f"{repo_path}/databricks/notebooks/{notebook}",
                              "source": "WORKSPACE"},
            "timeout_seconds": timeout,
            "max_retries": 0,  # retries are Airflow's job; avoids silent double compute
        }
        if previous:
            task["depends_on"] = [{"task_key": previous}]
        tasks.append(task)
        previous = key
    return {
        "name": JOB_NAME,
        "description": "Bank Health: bronze -> silver -> gold -> train (only if retrain=true) -> score",
        "max_concurrent_runs": 1,          # an Airflow retry can never start a parallel copy
        "queue": {"enabled": True},
        "timeout_seconds": 3 * 3600,
        "parameters": [
            {"name": "catalog", "default": catalog},
            {"name": "retrain", "default": "false"},
        ],
        "tags": {"project": "bank_health"},
        "tasks": tasks,
    }


def create_or_update_job(api: DatabricksAPI, settings: dict) -> int:
    """Create the job, or reset the existing job with the same name. Returns job_id."""
    jobs = api.call("GET", "/api/2.2/jobs/list", params={"name": settings["name"]}).get("jobs", [])
    if len(jobs) > 1:
        raise RuntimeError(f"{len(jobs)} jobs named {settings['name']}; expected at most one")
    if jobs:
        job_id = jobs[0]["job_id"]
        api.call("POST", "/api/2.2/jobs/reset", json={"job_id": job_id, "new_settings": settings})
        log.info("Updated job %s (%s)", settings["name"], job_id)
    else:
        job_id = api.call("POST", "/api/2.2/jobs/create", json=settings)["job_id"]
        log.info("Created job %s (%s)", settings["name"], job_id)
    return int(job_id)


if __name__ == "__main__":
    # Manual: python -m ingestion.databricks_jobs   (creates/updates the job, prints its id)
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from _env import load_env

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_env()
    api = DatabricksAPI.from_env()
    repo = pull_git_folder(api)
    job_id = create_or_update_job(api, job_settings(repo["path"], os.environ.get("DATABRICKS_CATALOG") or "bank_health"))
    print(f"job {JOB_NAME}: id {job_id}")
