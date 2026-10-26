"""Databricks job definition + helpers: sync the notebooks, create/update the job.

What runs where follows BANK_HEALTH_ENV (ingestion/environment.py):
    prod  job bank_health_pipeline      notebooks from the Git folder, pulled to main
    dev   job bank_health_pipeline_dev  notebooks uploaded from this checkout to a plain
                                        workspace folder (never a Git folder, so it can't be
                                        mistaken for prod's)

The job runs the notebooks in order on serverless compute:
    bronze -> silver -> gold -> train_model -> score
Its settings live here (versioned with the repo) and `create_or_update_job` is
idempotent: an existing job with the same name is reset to these settings.

`retrain` defaults to "false": weekly runs score with the current @champion model;
04_train_model only retrains when run with retrain=true.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import requests

from ingestion.environment import SETTINGS, EnvironmentMismatch, ProdWriteRefused, Settings
from ingestion.environment import settings as current_settings

log = logging.getLogger(__name__)

PROD_GIT_BRANCH = "main"
NOTEBOOK_DIR = Path(__file__).resolve().parent.parent / "databricks" / "notebooks"
# Serverless environment version for every notebook (Python 3.11, numpy 1.23). Training and
# scoring must share it: a pickled model may not load in another one. Model v5 was trained here.
# Change it only together with a retrain, and update docs/model_card.md and EXPECTED_ENVIRONMENT
# in databricks/notebooks/_common.py.
ENVIRONMENT_VERSION = "2"
ENVIRONMENTS = [{"environment_key": "pinned", "spec": {"environment_version": ENVIRONMENT_VERSION}}]
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
    def from_env(cls) -> DatabricksAPI:
        return cls(os.environ.get("DATABRICKS_HOST", ""), os.environ.get("DATABRICKS_TOKEN", ""))

    def __repr__(self) -> str:
        return f"DatabricksAPI(host={self.host!r})"

    def call(self, method: str, path: str, **kwargs) -> dict:
        resp = self.session.request(method, self.host + path, timeout=60, **kwargs)
        if resp.status_code >= 400:
            raise requests.HTTPError(
                f"Databricks {method} {path} failed: {resp.status_code} {resp.text[:300]}", response=resp
            )
        return resp.json() if resp.text else {}


def _me(api: DatabricksAPI) -> str:
    return api.call("GET", "/api/2.0/preview/scim/v2/Me")["userName"]


def find_git_folder(api: DatabricksAPI, folder: str) -> dict:
    """The Git folder at /Users/<me>/<folder>.

    Matched by path, not by repository URL: two Git folders of the same repo share a URL, and
    prod must never pick up another folder. The Repos list can be empty on Free Edition even
    for a real Git folder, so fall back to fetching it by its workspace id."""
    path = f"/Users/{_me(api)}/{folder}"
    for r in api.call("GET", "/api/2.0/repos").get("repos", []):
        if r.get("path", "").rstrip("/") == path:
            return r
    status = api.call("GET", "/api/2.0/workspace/get-status", params={"path": path})
    return api.call("GET", f"/api/2.0/repos/{status['resource_id']}")


def pull_git_folder(api: DatabricksAPI, folder: str, branch: str = PROD_GIT_BRANCH) -> dict:
    """Update the Git folder to the latest commit of `branch` (safe to repeat)."""
    repo = find_git_folder(api, folder)
    api.call("PATCH", f"/api/2.0/repos/{repo['id']}", json={"branch": branch})
    repo = api.call("GET", f"/api/2.0/repos/{repo['id']}")
    log.info("Git folder %s at %s @ %s", repo["path"], branch, repo.get("head_commit_id", "")[:7])
    return repo


def upload_notebooks(api: DatabricksAPI, folder: str, notebook_dir: Path = NOTEBOOK_DIR) -> str:
    """Copy this checkout's notebooks to /Users/<me>/<folder>/databricks/notebooks (a plain
    workspace folder). Returns the folder root. Refuses prod's Git folder."""
    if folder == SETTINGS["prod"].databricks_folder:
        raise ProdWriteRefused(f"refusing to upload notebooks over the prod Git folder {folder}")
    files = sorted(Path(notebook_dir).glob("*.py"))
    if not files:
        raise FileNotFoundError(f"no notebooks in {notebook_dir}")
    root = f"/Users/{_me(api)}/{folder}"
    target = f"{root}/databricks/notebooks"
    api.call("POST", "/api/2.0/workspace/mkdirs", json={"path": target})
    for f in files:
        api.call(
            "POST",
            "/api/2.0/workspace/import",
            json={
                "path": f"{target}/{f.stem}",
                "format": "SOURCE",
                "language": "PYTHON",
                "overwrite": True,
                "content": base64.b64encode(f.read_bytes()).decode(),
            },
        )
    log.info("Uploaded %s notebooks to %s", len(files), target)
    return root


def sync_notebooks(api: DatabricksAPI, s: Settings | None = None) -> str:
    """Make the job's notebooks current. Returns the workspace folder the job runs from.

    prod: pull the Git folder to main. dev: upload this checkout's notebooks."""
    s = s or current_settings()
    if s.databricks_folder is None:
        raise EnvironmentMismatch(f"BANK_HEALTH_ENV={s.name} has no Databricks job")
    if s.is_prod:
        return pull_git_folder(api, s.databricks_folder, PROD_GIT_BRANCH)["path"]
    return upload_notebooks(api, s.databricks_folder)


def job_settings(repo_path: str, s: Settings | None = None) -> dict:
    """Job definition for the current environment (or `s`). The notebooks receive `env` and
    `catalog` and stop if the two don't belong together."""
    s = s or current_settings()
    if s.databricks_job_name is None or s.databricks_catalog is None:
        raise EnvironmentMismatch(f"BANK_HEALTH_ENV={s.name} has no Databricks job")
    tasks, previous = [], None
    for key, notebook, timeout in NOTEBOOK_TASKS:
        task = {
            "task_key": key,
            "notebook_task": {
                "notebook_path": f"{repo_path}/databricks/notebooks/{notebook}",
                "source": "WORKSPACE",
            },
            "timeout_seconds": timeout,
            "max_retries": 0,  # retries are Airflow's job; avoids silent double compute
            "environment_key": "pinned",
        }
        if previous:
            task["depends_on"] = [{"task_key": previous}]
        tasks.append(task)
        previous = key
    return {
        "name": s.databricks_job_name,
        "description": "Bank Health: bronze -> silver -> gold -> train (only if retrain=true) -> score",
        "max_concurrent_runs": 1,  # an Airflow retry can never start a parallel copy
        "queue": {"enabled": True},
        "timeout_seconds": 3 * 3600,
        "parameters": [
            {"name": "catalog", "default": s.databricks_catalog},
            {"name": "env", "default": s.name},
            {"name": "retrain", "default": "false"},
        ],
        "tags": {"project": "bank_health", "env": s.name},
        "environments": ENVIRONMENTS,
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
    job = job_settings(sync_notebooks(api))
    job_id = create_or_update_job(api, job)
    print(f"job {job['name']}: id {job_id}")
