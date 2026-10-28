"""The service-level objective: this week's data is ready by Monday 12:00 America/Chicago.

"Ready" means the week's bank_health_weekly run succeeded (a PIPELINE_RUNS row with
STATE='success') and ended before the deadline. Used by:
- record_run, to set PIPELINE_RUNS.SLO_MET;
- the bank_health_slo_check DAG (Mondays 12:00 CT, on the prod machine);
- the GitHub Actions watchdog (scripts/late_run_watchdog.py), which still alerts when the
  whole VM is down.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
DEADLINE = time(12, 0)


def week_start(moment: datetime) -> datetime:
    """Monday 00:00 Chicago time of the week containing `moment`."""
    local = moment.astimezone(TZ)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime.combine(monday, time(0, 0), TZ)


def deadline(moment: datetime) -> datetime:
    """Monday 12:00 Chicago time of the week containing `moment` (DST handled by the zone)."""
    return datetime.combine(week_start(moment).date(), DEADLINE, TZ)


def slo_met(state: str | None, started_at: datetime | None, ended_at: datetime | None) -> bool | None:
    """True if the run succeeded by that week's deadline; None if it can't be judged."""
    if started_at is None or ended_at is None:
        return None
    return state == "success" and ended_at <= deadline(started_at)


def check_week(cur, now: datetime, table: str = "BANK_HEALTH.OPS.PIPELINE_RUNS") -> tuple[bool, str]:
    """(ok, message) for the week containing `now`. Before the deadline it is always ok."""
    start, due = week_start(now), deadline(now)
    if now < due:
        return True, f"before the deadline ({due:%a %d %b %H:%M %Z}); nothing to check yet"
    cur.execute(
        f"select RUN_ID, STATE, STARTED_AT, ENDED_AT from {table} "
        "where STARTED_AT >= %(start)s order by STARTED_AT",
        {"start": start},
    )
    rows = cur.fetchall()
    on_time = [r for r in rows if r[1] == "success" and r[3] is not None and r[3] <= due]
    if on_time:
        run_id, _, _, ended = on_time[0]
        return True, f"run {run_id} succeeded at {ended.astimezone(TZ):%a %H:%M %Z}, before {due:%H:%M %Z}"
    if not rows:
        found = "no run has started this week"
    else:
        found = "; ".join(
            f"{r[0]}: {r[1]}" + (f", ended {r[3].astimezone(TZ):%a %H:%M}" if r[3] else ", not finished")
            for r in rows
        )
    return False, f"no successful run by {due:%a %d %b %H:%M %Z}: {found}"
