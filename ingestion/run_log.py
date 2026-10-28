"""The run log: one OPS.PIPELINE_RUNS row per DAG run (finding O-03, cost per run C-02).

Written by the DAG's last task, record_run, which runs even when earlier tasks failed
(trigger rule all_done). Each figure is collected on its own and left NULL if its source isn't
available (a run that failed early has no dbt results), so a failed run still gets its row.

Sources:
- state and failed tasks: Airflow's task states for this run;
- bronze / silver / gold rows, data-quality warnings, model used: the Databricks job's notebook
  exit summaries (Jobs API runs/get-output);
- MARTS rows, scored banks, latest FDIC quarter: Snowflake counts;
- dbt results: OPS.DBT_RESULTS, written by the dbt on-run-end hook (macros/record_dbt_results.sql);
- credits: INFORMATION_SCHEMA.WAREHOUSE_METERING_HISTORY for the run's warehouse over the run
  window. Metering is hourly, so the estimate includes anything else that used the warehouse in
  those hours (e.g. a Power BI refresh). The next run recomputes it as CREDITS_FINAL, once
  metering is complete. Needs MONITOR USAGE (prod only); dev records NULL.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ingestion import slo

log = logging.getLogger(__name__)

TABLE = "OPS.PIPELINE_RUNS"
MARTS_TABLES = ("DIM_BANK", "FCT_BANK_QUARTER", "FCT_COMPLAINTS", "MART_BANK_SCORECARD", "MART_EARLY_WARNING")
COLUMNS = (
    "RUN_ID", "ENVIRONMENT", "HOST_ID", "DAG_ID", "STARTED_AT", "ENDED_AT", "DURATION_SECONDS", "STATE",
    "FAILED_TASKS", "BRONZE_ROWS", "SILVER_ROWS", "GOLD_ROWS", "MARTS_ROWS", "SCORED_BANKS", "ROWS_DETAIL",
    "DBT_MODELS_OK", "DBT_MODELS_ERROR", "DBT_TESTS_PASS", "DBT_TESTS_WARN", "DBT_TESTS_FAIL", "DQ_WARNINGS",
    "CREDITS_ESTIMATE", "MODEL_NAME", "MODEL_VERSION", "MODEL_ALIAS", "LATEST_FDIC_QUARTER",
    "VOLUME_ANOMALIES", "SLO_MET",
)  # fmt: skip
VARIANT_COLUMNS = {"ROWS_DETAIL", "VOLUME_ANOMALIES"}
_MODEL_RE = re.compile(r"^(?P<name>\S+) v(?P<version>\S+) \(@(?P<alias>[\w-]+)\)$")


# ------------------------------------------------------------------ run outcome
def run_outcome(task_states: dict[str, str | None], this_task: str) -> tuple[str, list[str]]:
    """("success" | "failed", failed task ids). Tasks that failed only because an upstream task
    failed (upstream_failed) make the run failed but aren't listed: the root causes are."""
    states = {task: state for task, state in task_states.items() if task != this_task}
    failed = sorted(task for task, state in states.items() if state == "failed")
    upstream_failed = any(state == "upstream_failed" for state in states.values())
    return ("failed" if failed or upstream_failed else "success"), failed


# ------------------------------------------------------------------ Databricks
def databricks_outputs(api, run_id: int | str) -> dict[str, dict]:
    """{task_key: notebook exit summary} for a finished job run (tasks without output skipped)."""
    run = api.call("GET", "/api/2.2/jobs/runs/get", params={"run_id": run_id})
    out: dict[str, dict] = {}
    for task in run.get("tasks", []):
        result = api.call("GET", "/api/2.2/jobs/runs/get-output", params={"run_id": task["run_id"]})
        text = (result.get("notebook_output") or {}).get("result")
        if not text:
            continue
        try:
            out[task["task_key"]] = json.loads(text)
        except json.JSONDecodeError:
            log.warning("task %s: exit value is not JSON", task["task_key"])
    return out


def layer_rows(outputs: dict[str, dict]) -> dict[str, Any]:
    """Row totals per layer, data-quality warnings and the scoring model, from the summaries."""
    bronze = {k: v.get("total_rows") for k, v in (outputs.get("bronze") or {}).items() if isinstance(v, dict)}
    silver = dict((outputs.get("silver") or {}).get("rows") or {})
    gold = (outputs.get("gold") or {}).get("bank_quarter_rows")
    score = outputs.get("score") or {}
    warnings = [
        ((outputs.get(task) or {}).get("data_quality") or {}).get("warnings")
        for task in ("silver", "gold", "score")
    ]
    model = _MODEL_RE.match(str(score.get("model") or ""))
    return {
        "BRONZE_ROWS": sum(v for v in bronze.values() if v) or None,
        "SILVER_ROWS": sum(v for v in silver.values() if v) or None,
        "GOLD_ROWS": gold,
        "DQ_WARNINGS": sum(w for w in warnings if w) if any(w is not None for w in warnings) else None,
        "MODEL_NAME": model["name"] if model else None,
        "MODEL_VERSION": model["version"] if model else None,
        "MODEL_ALIAS": model["alias"] if model else None,
        "detail": {"bronze": bronze or None, "silver": silver or None, "gold": {"bank_quarter": gold}},
    }


# ------------------------------------------------------------------ Snowflake
def marts_counts(cur) -> dict[str, int]:
    counts = {}
    for table in MARTS_TABLES:
        cur.execute(f"select count(*) from MARTS.{table}")
        counts[table] = cur.fetchone()[0]
    return counts


def latest_fdic_quarter(cur):
    cur.execute("select max(REPORT_DATE) from MARTS.FCT_BANK_QUARTER")
    return cur.fetchone()[0]


def dbt_counts(cur, run_id: str) -> dict[str, int]:
    """Model and test outcomes for this run. Cosmos retries add rows: the latest per node counts."""
    cur.execute(
        "select RESOURCE_TYPE, STATUS, count(*) from ("
        "  select RESOURCE_TYPE, STATUS from OPS.DBT_RESULTS where AIRFLOW_RUN_ID = %(run_id)s"
        "  qualify row_number() over (partition by NODE_ID order by RECORDED_AT desc) = 1"
        ") group by 1, 2",
        {"run_id": run_id},
    )
    by = {(rtype, status): n for rtype, status, n in cur.fetchall()}
    if not by:
        return {}
    return {
        "DBT_MODELS_OK": by.get(("model", "success"), 0),
        "DBT_MODELS_ERROR": by.get(("model", "error"), 0),
        "DBT_TESTS_PASS": by.get(("test", "pass"), 0),
        "DBT_TESTS_WARN": by.get(("test", "warn"), 0),
        "DBT_TESTS_FAIL": by.get(("test", "fail"), 0) + by.get(("test", "error"), 0),
    }


def credits_used(cur, warehouse: str, start: datetime, end: datetime) -> float | None:
    """Warehouse credits metered in the hours the run touched. None without MONITOR USAGE."""
    try:
        cur.execute(
            "select sum(CREDITS_USED), count(*) from table(INFORMATION_SCHEMA.WAREHOUSE_METERING_HISTORY("
            "date_range_start => %(start)s::timestamp_ltz, date_range_end => %(end)s::timestamp_ltz, "
            "warehouse_name => %(wh)s))",
            {"start": start, "end": end, "wh": warehouse},
        )
        total, hours = cur.fetchone()
        # No metering rows means "no data" (e.g. dev, which has no MONITOR USAGE, gets an empty
        # result rather than an error), not "zero credits": record NULL, never a misleading 0.
        return float(total) if hours else None
    except Exception as exc:  # no privilege in dev, or metering unavailable
        log.info("credits not available: %s", type(exc).__name__)
        return None


def backfill_final_credits(cur, warehouse: str, now: datetime, settle_hours: int = 4) -> int:
    """Fill CREDITS_FINAL for earlier runs whose metering is complete. Returns rows updated."""
    cur.execute(
        f"select RUN_ID, STARTED_AT, ENDED_AT from {TABLE} where CREDITS_FINAL is null "
        "and ENDED_AT < %(settled)s and ENDED_AT > %(oldest)s",
        {"settled": now - timedelta(hours=settle_hours), "oldest": now - timedelta(days=60)},
    )
    updated = 0
    for run_id, started, ended in cur.fetchall():
        credits = credits_used(cur, warehouse, started, ended)
        if credits is None:
            break
        cur.execute(
            f"update {TABLE} set CREDITS_FINAL = %(c)s where RUN_ID = %(run_id)s",
            {"c": credits, "run_id": run_id},
        )
        updated += 1
    return updated


# ------------------------------------------------------------------ the row
class InvalidRunTimes(ValueError):
    """The run's start or end time can't be right; no row is written from it."""


def _to_utc(moment: datetime | None, name: str) -> datetime:
    if moment is None:
        raise InvalidRunTimes(f"{name} is missing")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise InvalidRunTimes(f"{name} has no time zone: {moment!r}")
    return moment.astimezone(UTC)


def build_row(
    *,
    run_id: str,
    environment: str,
    host_id: str | None,
    dag_id: str,
    started_at: datetime,
    ended_at: datetime,
    state: str,
    scheduled: bool,
    failed_tasks: list[str],
    layers: dict[str, Any] | None = None,
    marts: dict[str, int] | None = None,
    dbt: dict[str, int] | None = None,
    credits: float | None = None,
    latest_quarter=None,
    volume_anomalies: list[dict] | None = None,
) -> dict[str, Any]:
    """One PIPELINE_RUNS row. Times are stored in UTC. A run that ended before it started
    (e.g. `airflow dags test` with a future logical date) raises InvalidRunTimes instead of
    writing a misleading row. SLO_MET is judged only for scheduled runs (NULL for manual ones:
    a manual run is not 'this week's run')."""
    started = _to_utc(started_at, "STARTED_AT")
    ended = _to_utc(ended_at, "ENDED_AT")
    if ended < started:
        raise InvalidRunTimes(
            f"ENDED_AT {ended:%Y-%m-%d %H:%M:%S}Z is before STARTED_AT {started:%Y-%m-%d %H:%M:%S}Z "
            f"for run {run_id}: not writing a row with a negative duration"
        )
    layers = layers or {}
    row: dict[str, Any] = dict.fromkeys(COLUMNS)
    row.update(
        RUN_ID=run_id,
        ENVIRONMENT=environment,
        HOST_ID=host_id,
        DAG_ID=dag_id,
        STARTED_AT=started,
        ENDED_AT=ended,
        DURATION_SECONDS=round((ended - started).total_seconds()),
        STATE=state,
        FAILED_TASKS=", ".join(failed_tasks) or None,
        MARTS_ROWS=sum(marts.values()) if marts else None,
        SCORED_BANKS=(marts or {}).get("MART_EARLY_WARNING"),
        CREDITS_ESTIMATE=round(credits, 4) if credits is not None else None,
        LATEST_FDIC_QUARTER=latest_quarter,
        VOLUME_ANOMALIES=volume_anomalies or None,
        SLO_MET=slo.slo_met(state, started, ended) if scheduled else None,
    )
    for key in (
        "BRONZE_ROWS",
        "SILVER_ROWS",
        "GOLD_ROWS",
        "DQ_WARNINGS",
        "MODEL_NAME",
        "MODEL_VERSION",
        "MODEL_ALIAS",
    ):
        row[key] = layers.get(key)
    row.update(dbt or {})
    detail = dict(layers.get("detail") or {})
    if marts:
        detail["marts"] = marts
    row["ROWS_DETAIL"] = detail or None
    return row


def merge_sql() -> str:
    """One MERGE on RUN_ID: inserts the run's row, or replaces it if record_run is retried.
    A single atomic statement, so a retry after a timeout can neither duplicate the row nor
    leave the run without one."""
    source = ", ".join(
        (f"parse_json(%({c})s)" if c in VARIANT_COLUMNS else f"%({c})s") + f" as {c}" for c in COLUMNS
    )
    updates = ", ".join(f"t.{c} = s.{c}" for c in COLUMNS if c != "RUN_ID")
    return (
        f"merge into {TABLE} t using (select {source}) s on t.RUN_ID = s.RUN_ID "
        f"when matched then update set {updates}, t.RECORDED_AT = current_timestamp() "
        f"when not matched then insert ({', '.join(COLUMNS)}) values ({', '.join('s.' + c for c in COLUMNS)})"
    )


def write_row(cur, row: dict[str, Any]) -> None:
    """Write this run's row with one MERGE on RUN_ID (idempotent under Airflow retries)."""
    params = {
        c: (json.dumps(row[c], default=str) if c in VARIANT_COLUMNS and row[c] is not None else row[c])
        for c in COLUMNS
    }
    cur.execute(merge_sql(), params)


def success_message(row: dict[str, Any]) -> tuple[str, list[str]]:
    """The weekly 'it ran fine' alert, so silence never means success."""
    minutes = (row.get("DURATION_SECONDS") or 0) / 60

    def n(value):
        return f"{value:,}" if isinstance(value, int) else "n/a"

    lines = [
        f"Run {row['RUN_ID']} on {row.get('HOST_ID') or '?'}: {minutes:.0f} min",
        f"Rows: bronze {n(row.get('BRONZE_ROWS'))}, silver {n(row.get('SILVER_ROWS'))}, "
        f"gold {n(row.get('GOLD_ROWS'))}, marts {n(row.get('MARTS_ROWS'))}",
        f"Scored banks: {n(row.get('SCORED_BANKS'))} (latest FDIC quarter {row.get('LATEST_FDIC_QUARTER')}, "
        f"model {row.get('MODEL_NAME') or '?'} v{row.get('MODEL_VERSION') or '?'} @{row.get('MODEL_ALIAS') or '?'})",
        f"dbt: {n(row.get('DBT_TESTS_PASS'))} tests passed, {n(row.get('DBT_TESTS_WARN'))} warned, "
        f"{n(row.get('DBT_TESTS_FAIL'))} failed",
        f"Credits (estimate): {row['CREDITS_ESTIMATE']}" if row.get("CREDITS_ESTIMATE") is not None else "",
        "Data ready before Monday 12:00 CT: "
        + {True: "yes", False: "no", None: "n/a (manual run)"}[row.get("SLO_MET")],
    ]
    return f"OK: {row['DAG_ID']} succeeded", [line for line in lines if line]
