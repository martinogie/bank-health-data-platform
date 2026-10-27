"""One scheduler only: the prod DAG runs on exactly one machine.

BANK_HEALTH.OPS.PROD_SCHEDULER holds one row naming the machine allowed to run prod (today
'pc', after cutover 'oracle-vm'). Each machine's .env sets PROD_HOST_ID. check_environment,
the first task of every prod run, calls check(): on any other machine the run stops at once,
with an alert, before it downloads or writes anything. So even if the DAG were unpaused on
both machines by mistake, only one could get past its first task.

Cutover changes that one row (docs/runbooks/cutover_and_rollback.md). Dev runs don't use it.
"""

from __future__ import annotations

TABLE = "BANK_HEALTH.OPS.PROD_SCHEDULER"


class SchedulerNotAuthorized(RuntimeError):
    """This machine is not the one allowed to run the prod schedule."""


def check(cur, host_id: str | None, table: str = TABLE) -> str:
    """Return the authorised host if it is this machine; raise SchedulerNotAuthorized otherwise."""
    host = (host_id or "").strip()
    if not host:
        raise SchedulerNotAuthorized(
            "PROD_HOST_ID is not set in this machine's .env (e.g. PROD_HOST_ID=pc or oracle-vm). "
            "Prod runs only on the machine named in " + table + "."
        )
    cur.execute(f"select HOST_ID, SET_AT, SET_BY from {table}")
    rows = cur.fetchall()
    if len(rows) != 1:
        raise SchedulerNotAuthorized(f"{table} must have exactly one row, found {len(rows)}. Not running.")
    allowed, set_at, set_by = rows[0]
    if (allowed or "").strip() != host:
        raise SchedulerNotAuthorized(
            f"This machine is '{host}', but prod is assigned to '{allowed}' (set {set_at} by {set_by}). "
            "Not running: only one machine may run the prod schedule. Pause the DAG here."
        )
    return host
