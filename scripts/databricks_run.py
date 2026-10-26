"""Pull the Databricks Git folder to the latest main, then run notebooks one by one.

One-off serverless runs (Jobs API runs/submit): nothing is saved as a job.
Prints each notebook's exit summary, or its error.

Usage (from the project folder):
    .venv\\Scripts\\python scripts/databricks_run.py 01_bronze 02_silver 03_gold
    (add --local to run this checkout's notebooks, --param=name=value to pass a widget value)

--local uploads databricks/notebooks/*.py from this checkout to a scratch workspace folder
(/Users/<me>/bank-health-local-run) and runs them from there, so an unpushed branch can be
tested. The notebooks still read and write the real catalog tables.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time

from pathlib import Path

import requests

from _env import ROOT, load_env

sys.path.insert(0, str(ROOT))
from ingestion.databricks_jobs import ENVIRONMENTS  # noqa: E402

REPO_NAME = "bank-health-data-platform"
LOCAL_FOLDER = "bank-health-local-run"
NOTEBOOK_DIR = Path(__file__).resolve().parents[1] / "databricks" / "notebooks"
POLL_SECONDS = 15


class Databricks:
    def __init__(self):
        self.host = os.environ["DATABRICKS_HOST"].rstrip("/")
        self.s = requests.Session()
        self.s.headers["Authorization"] = "Bearer " + os.environ["DATABRICKS_TOKEN"]

    def call(self, method, path, **kw):
        r = self.s.request(method, self.host + path, timeout=60, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.text else {}


def find_and_pull_repo(db: Databricks) -> str:
    repos = db.call("GET", "/api/2.0/repos").get("repos", [])
    mine = [r for r in repos if r.get("url", "").rstrip("/").removesuffix(".git").endswith(REPO_NAME)]
    if mine:
        repo = mine[0]
    else:
        # On Free Edition the Repos API list can be empty even for a real Git folder,
        # but the folder can still be fetched by its id (the workspace resource_id).
        me = db.call("GET", "/api/2.0/preview/scim/v2/Me")["userName"]
        status = db.call("GET", "/api/2.0/workspace/get-status", params={"path": f"/Users/{me}/{REPO_NAME}"})
        repo = db.call("GET", f"/api/2.0/repos/{status['resource_id']}")
    db.call("PATCH", f"/api/2.0/repos/{repo['id']}", json={"branch": "main"})  # pulls latest main
    head = db.call("GET", f"/api/2.0/repos/{repo['id']}").get("head_commit_id", "")[:7]
    print(f"Git folder {repo['path']} pulled to main @ {head}")
    return repo["path"]


def upload_local_notebooks(db: Databricks) -> str:
    """Copy this checkout's notebooks to a scratch workspace folder; returns its root path."""
    me = db.call("GET", "/api/2.0/preview/scim/v2/Me")["userName"]
    root = f"/Users/{me}/{LOCAL_FOLDER}"
    target = f"{root}/databricks/notebooks"
    db.call("POST", "/api/2.0/workspace/mkdirs", json={"path": target})
    files = sorted(NOTEBOOK_DIR.glob("*.py"))
    for f in files:
        db.call(
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
    print(f"Uploaded {len(files)} local notebooks to {target}")
    return root


def run_notebook(db: Databricks, repo_path: str, notebook: str, params: dict) -> bool:
    run = db.call(
        "POST",
        "/api/2.2/jobs/runs/submit",
        json={
            "run_name": f"bank_health {notebook} (manual)",
            "environments": ENVIRONMENTS,  # same serverless environment as the scheduled job
            "tasks": [
                {
                    "task_key": notebook,
                    "environment_key": "pinned",
                    "notebook_task": {
                        "notebook_path": f"{repo_path}/databricks/notebooks/{notebook}",
                        "source": "WORKSPACE",
                        "base_parameters": params,
                    },
                }
            ],
        },
    )
    run_id = run["run_id"]
    print(f"\n=== {notebook}: run {run_id} started")
    started = time.time()
    while True:
        info = db.call("GET", "/api/2.2/jobs/runs/get", params={"run_id": run_id})
        state = info["state"]
        if state["life_cycle_state"] in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            break
        time.sleep(POLL_SECONDS)
    minutes = (time.time() - started) / 60
    result = state.get("result_state", state["life_cycle_state"])
    print(f"=== {notebook}: {result} in {minutes:.1f} min  ({info.get('run_page_url', '')})")

    task_run_id = info["tasks"][0]["run_id"]
    out = db.call("GET", "/api/2.2/jobs/runs/get-output", params={"run_id": task_run_id})
    if out.get("notebook_output", {}).get("result"):
        try:
            print(json.dumps(json.loads(out["notebook_output"]["result"]), indent=2))
        except json.JSONDecodeError:
            print(out["notebook_output"]["result"])
    if out.get("error"):
        print("ERROR:", out["error"])
        print((out.get("error_trace") or "")[-3000:])
    return result == "SUCCESS"


def main() -> None:
    args = sys.argv[1:]
    notebooks = [a for a in args if not a.startswith("--")] or ["01_bronze", "02_silver", "03_gold"]
    load_env()
    params = {"catalog": os.environ.get("DATABRICKS_CATALOG") or "bank_health"}
    for a in args:
        if a.startswith("--param="):  # --param=retrain=false
            k, v = a.removeprefix("--param=").split("=", 1)
            params[k] = v
    db = Databricks()
    repo_path = upload_local_notebooks(db) if "--local" in args else find_and_pull_repo(db)
    for nb in notebooks:
        if not run_notebook(db, repo_path, nb, params):
            raise SystemExit(f"{nb} failed; stopping.")


if __name__ == "__main__":
    main()
