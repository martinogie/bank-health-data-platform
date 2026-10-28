"""Runs INSIDE the Airflow scheduler container (piped in by test_airflow_stack.py).

Parses the dags/ folder exactly as Airflow does and prints the facts the smoke
test checks, as JSON. Needs no connections or secrets.
"""

import json
import logging
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

from airflow.dag_processing.dagbag import DagBag  # noqa: E402

bag = DagBag("/opt/airflow/dags")
facts = {
    "dag_ids": sorted(bag.dags),
    "import_errors": {k: str(v)[:500] for k, v in bag.import_errors.items()},
}

dag = bag.dags.get("bank_health_weekly")
if dag is not None:
    facts["bank_health_weekly"] = {
        "timetable": {
            "expression": getattr(dag.timetable, "expression", None),
            "timezone": str(getattr(dag.timetable, "timezone", None) or dag.timezone),
        },
        "catchup": dag.catchup,
        "max_active_runs": dag.max_active_runs,
        "has_doc_md": bool(dag.doc_md),
        "tasks": {
            t.task_id: {
                "retries": t.retries,
                "trigger_rule": str(getattr(t.trigger_rule, "value", t.trigger_rule)),
                "has_failure_alert": bool(getattr(t, "on_failure_callback", None)),
                "upstream": sorted(t.upstream_task_ids),
                "has_doc_md": bool(t.doc_md),
            }
            for t in dag.tasks
        },
    }

print("DAG_FACTS_JSON=" + json.dumps(facts))
