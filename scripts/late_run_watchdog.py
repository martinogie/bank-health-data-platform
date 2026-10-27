"""Late-run watchdog, run from GitHub Actions (.github/workflows/late_run_watchdog.yml).

The same check as the bank_health_slo_check DAG, made from outside the prod machine: if the VM
is down, its own check can't run, but this one still reads BANK_HEALTH.OPS.PIPELINE_RUNS and
posts to the prod Slack and Discord channels.

Logs in as the CI service user (read-only on prod). Exit codes: 0 = on time (or too early to
tell), 1 = late (alert sent), 2 = couldn't check (alert sent).
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingestion import slo  # noqa: E402
from ingestion.alerts import mask, notify  # noqa: E402

TABLE = "BANK_HEALTH.OPS.PIPELINE_RUNS"


def main(now: datetime | None = None) -> int:
    logging.getLogger("snowflake").setLevel(logging.ERROR)  # login URLs contain the account
    now = now or datetime.now(UTC)
    try:
        from ingestion.snowflake_load import connect_from_env

        conn = connect_from_env()
        try:
            ok, message = slo.check_week(conn.cursor(), now, table=TABLE)
        finally:
            conn.close()
    except Exception as exc:
        problem = mask(f"{type(exc).__name__}: {exc}")[:500]
        print(f"could not check: {problem}")
        notify(
            "WATCHDOG: could not check this week's run",
            [problem, "Check Snowflake and the CI service user; see docs/runbooks/alerts.md."],
            env="prod watchdog",
        )
        return 2
    print(message)
    if ok:
        return 0
    notify(
        "LATE (seen from GitHub): this week's bank data is not ready (objective: Monday 12:00 CT)",
        [message, "The VM may be down. What to do: docs/runbooks/alerts.md, 'Late run'."],
        env="prod watchdog",
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
