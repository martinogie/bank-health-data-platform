r"""Run Databricks notebooks one by one, in the environment set by BANK_HEALTH_ENV.

    prod  pulls the Git folder to the latest main, runs against catalog bank_health
    dev   uploads this checkout's notebooks to a plain workspace folder, runs against
          bank_health_dev (so an unpushed branch can be tested without touching prod)

One-off serverless runs (Jobs API runs/submit): nothing is saved as a job.
Prints each notebook's exit summary, or its error.

Usage (from the project folder; BANK_HEALTH_ENV comes from .env):
    .venv\Scripts\python scripts/databricks_run.py 01_bronze 02_silver 03_gold
    (add --param=name=value to pass a widget value, e.g. --param=model_alias=candidate)
"""

from __future__ import annotations

import json
import sys
import time

from _env import ROOT, load_env

sys.path.insert(0, str(ROOT))
from ingestion import databricks_jobs as dj  # noqa: E402
from ingestion import environment  # noqa: E402
from ingestion.databricks_jobs import ENVIRONMENTS  # noqa: E402

POLL_SECONDS = 15
FIXED_PARAMS = ("env", "catalog")  # follow BANK_HEALTH_ENV; never passed by hand


def run_notebook(db: dj.DatabricksAPI, repo_path: str, notebook: str, params: dict) -> bool:
    run = db.call(
        "POST",
        "/api/2.2/jobs/runs/submit",
        json={
            "run_name": f"bank_health {notebook} ({params['env']}, manual)",
            "environments": ENVIRONMENTS,  # same serverless environment as the scheduled job
            "tasks": [
                {
                    "task_key": notebook.replace("/", "__"),  # task keys allow only letters, digits, - and _
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
    if "--local" in args:
        raise SystemExit(
            "--local is gone: set BANK_HEALTH_ENV=dev instead (dev always runs this checkout's "
            "notebooks against bank_health_dev). Prod only runs main."
        )
    notebooks = [a for a in args if not a.startswith("--")] or ["01_bronze", "02_silver", "03_gold"]
    load_env()
    s = environment.settings()
    params = {"catalog": environment.databricks_catalog(), "env": s.name}
    for a in args:
        if a.startswith("--param="):  # --param=retrain=false
            k, v = a.removeprefix("--param=").split("=", 1)
            if k in FIXED_PARAMS:
                raise SystemExit(f"--param={k}=... is not allowed: {k} follows BANK_HEALTH_ENV")
            params[k] = v
    print(environment.describe())
    db = dj.DatabricksAPI.from_env()
    repo_path = dj.sync_notebooks(db, s)
    for nb in notebooks:
        if not run_notebook(db, repo_path, nb, params):
            raise SystemExit(f"{nb} failed; stopping.")


if __name__ == "__main__":
    main()
