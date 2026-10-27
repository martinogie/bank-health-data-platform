"""Freshness check, the last gate before the watchlist is published (finding O-02).

A run can succeed task by task and still leave stale data. check() fails the run (and so
alerts) if any of these is not true:
1. every MARTS table was rebuilt during this run (LAST_ALTERED after the run started);
2. the latest FDIC quarter in the marts is the one expected for today's date;
3. the number of scored banks (the watchlist rows) is within the expected range.

Expected FDIC quarter: call reports are due 30 days after quarter end (35 for banks with
foreign offices) and the FDIC publishes the full set some weeks later. A quarter is expected
from 75 days after its end: e.g. 30 June is expected from 13 September. A newer quarter than
expected is fine (published early).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

PUBLISH_LAG_DAYS = 75
# Scored banks when there is no baseline yet. US FDIC-insured banks: about 4,400 in 2026,
# falling about 3% a year through mergers; 4,257 scored on 26 Oct 2026.
SCORED_BANKS_FALLBACK = (3_500, 5_500)
SCORED_BANKS_TOLERANCE = 0.03  # +-3% of the median of recent runs (see volume_checks)


def quarter_ends_before(day: date) -> list[date]:
    out, y = [], day.year
    for year in (y, y - 1):
        out += [date(year, 12, 31), date(year, 9, 30), date(year, 6, 30), date(year, 3, 31)]
    return [q for q in out if q <= day]


def expected_fdic_quarter(today: date, lag_days: int = PUBLISH_LAG_DAYS) -> date:
    """The newest quarter end that should be published by `today`."""
    return next(q for q in quarter_ends_before(today) if q + timedelta(days=lag_days) <= today)


def scored_banks_range(baseline_median: float | None) -> tuple[int, int]:
    if not baseline_median:
        return SCORED_BANKS_FALLBACK
    return (
        int(baseline_median * (1 - SCORED_BANKS_TOLERANCE)),
        int(baseline_median * (1 + SCORED_BANKS_TOLERANCE)) + 1,
    )


def check(cur, run_started_at: datetime, today: date, scored_banks_median: float | None = None) -> dict:
    """Run the three checks against the current Snowflake database. Returns a summary with a
    `problems` list (empty when everything is fresh)."""
    problems: list[str] = []

    cur.execute(
        "select TABLE_NAME, LAST_ALTERED from INFORMATION_SCHEMA.TABLES "
        "where TABLE_SCHEMA = 'MARTS' and TABLE_TYPE = 'BASE TABLE' order by TABLE_NAME"
    )
    marts = cur.fetchall()
    if not marts:
        problems.append("no MARTS tables found")
    stale = [name for name, altered in marts if altered is None or altered < run_started_at]
    if stale:
        problems.append(f"MARTS not rebuilt in this run: {', '.join(stale)}")

    cur.execute("select max(REPORT_DATE) from MARTS.FCT_BANK_QUARTER")
    latest = cur.fetchone()[0]
    expected = expected_fdic_quarter(today)
    if latest is None or latest < expected:
        problems.append(f"latest FDIC quarter is {latest}, expected {expected} or newer for {today}")

    cur.execute("select count(*) from MARTS.MART_EARLY_WARNING")
    scored = cur.fetchone()[0]
    low, high = scored_banks_range(scored_banks_median)
    if not low <= scored <= high:
        problems.append(f"{scored:,} scored banks, expected {low:,} to {high:,}")

    return {
        "marts_checked": len(marts),
        "latest_fdic_quarter": str(latest),
        "expected_fdic_quarter": str(expected),
        "scored_banks": scored,
        "scored_banks_range": [low, high],
        "problems": problems,
    }
