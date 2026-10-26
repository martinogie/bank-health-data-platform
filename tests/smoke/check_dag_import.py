"""DAG import check, run INSIDE the custom Airflow image (CI job `dag-import`, or by hand).

Loads airflow/dags exactly as Airflow does (DagBag, Cosmos rendering the dbt project with
`dbt ls`, which needs no Snowflake connection), three times in fresh processes:
    BANK_HEALTH_ENV=prod    must import with no errors, and target prod objects
    BANK_HEALTH_ENV=dev     must import with no errors, and target dev objects
    BANK_HEALTH_ENV unset   must FAIL to import, naming BANK_HEALTH_ENV (no default)
Exit code 0 when all three behave; prints a short report. Needs no secrets: the Snowflake
values are placeholders, because nothing connects while a DAG is parsed.

    docker run --rm -v "$PWD:/repo" -w /repo bank-health-airflow:ci python tests/smoke/check_dag_import.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

DAGS = os.environ.get("DAGS_FOLDER", "/opt/airflow/dags")
PLACEHOLDERS = {
    "SNOWFLAKE_ACCOUNT": "placeholder",
    "SNOWFLAKE_USER": "placeholder",
    "SNOWFLAKE_ROLE": "placeholder",
    "SNOWFLAKE_WAREHOUSE": "placeholder",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "/nonexistent.p8",
    "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    # Cosmos caches `dbt ls` in Airflow's metadata database, which this bare container doesn't
    # have; without the cache it simply runs dbt ls each time (same DAG, slower parse).
    "AIRFLOW__COSMOS__ENABLE_CACHE_DBT_LS": "False",
}

PARSE = r"""
import json, logging, warnings
warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
from airflow.dag_processing.dagbag import DagBag
bag = DagBag(DAGS)
dag = bag.dags.get("bank_health_weekly")
out = {"dag_ids": sorted(bag.dags), "import_errors": {k: str(v)[-600:] for k, v in bag.import_errors.items()}}
if dag is not None:
    out["tasks"] = sorted(t.task_id for t in dag.tasks)
    run = dag.get_task("run_databricks_job")
    out["job_name"] = run.job_name
    params = getattr(run, "job_parameters", None) or (getattr(run, "json", None) or {}).get("job_parameters") or {}
    out["job_parameters"] = {k: v for k, v in params.items() if k != "retrain"}
    out["tags"] = sorted(dag.tags)
print("RESULT=" + json.dumps(out))
""".replace("DAGS", repr(DAGS))


def parse(env: str | None) -> dict:
    child = {**os.environ, **PLACEHOLDERS}
    child.pop("BANK_HEALTH_ENV", None)
    child.pop("SNOWFLAKE_DATABASE", None)
    child.pop("DATABRICKS_CATALOG", None)
    if env is not None:
        child["BANK_HEALTH_ENV"] = env
    proc = subprocess.run(
        [sys.executable, "-c", PARSE], env=child, capture_output=True, text=True, timeout=600
    )
    line = next((x for x in proc.stdout.splitlines() if x.startswith("RESULT=")), None)
    if line is None:
        raise SystemExit(f"DagBag did not finish for env={env}:\n{proc.stderr[-3000:]}")
    return json.loads(line.removeprefix("RESULT="))


def main() -> int:
    problems: list[str] = []
    expected = {
        "prod": ("bank_health_pipeline", {"catalog": "bank_health", "env": "prod"}),
        "dev": ("bank_health_pipeline_dev", {"catalog": "bank_health_dev", "env": "dev"}),
    }
    for env, (job, params) in expected.items():
        r = parse(env)
        if r["import_errors"]:
            problems.append(f"{env}: import errors {r['import_errors']}")
            continue
        if "bank_health_weekly" not in r["dag_ids"]:
            problems.append(f"{env}: bank_health_weekly missing ({r['dag_ids']})")
            continue
        dbt_tasks = [t for t in r["tasks"] if t.startswith("dbt_build.")]
        if r["job_name"] != job or r["job_parameters"] != params or env not in r["tags"]:
            problems.append(f"{env}: targets {r['job_name']} {r['job_parameters']} tags {r['tags']}")
        for needed in ("check_environment", "publish_dbt_manifest", "export_watchlist"):
            if needed not in r["tasks"]:
                problems.append(f"{env}: task {needed} missing")
        if not dbt_tasks:
            problems.append(f"{env}: Cosmos rendered no dbt tasks")
        print(
            f"[{env}] imported: {len(r['tasks'])} tasks ({len(dbt_tasks)} dbt), job {r['job_name']} {r['job_parameters']}"
        )

    r = parse(None)
    errors = " ".join(r["import_errors"].values())
    if "bank_health_weekly" in r["dag_ids"] or "BANK_HEALTH_ENV is not set" not in errors:
        problems.append(f"unset: expected an import error naming BANK_HEALTH_ENV, got {r}")
    else:
        print("[unset] refused as expected: BANK_HEALTH_ENV is not set")

    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
