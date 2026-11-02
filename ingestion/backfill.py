"""Backfill: redo a past period through the whole weekly pipeline (D-02).

A backfill is a manual run of `bank_health_weekly` with dates in its trigger form (or from
`scripts/backfill.py`). It re-downloads only the period asked for, then runs every normal step:
contracts, upload, bronze, silver (newest load wins), gold, scoring with @champion, Snowflake RAW,
dbt with write-audit-publish, and all checks.

The rules (checked in the run's first steps, before anything is downloaded):
- FDIC: the quarters whose quarter-end falls in [from, to]; at least one; from the FDIC history
  start (2000-03-31) up to the FDIC watermark (the newest quarter the weekly run has loaded).
- CFPB: complaints received in [from, to]; from the start of the 3 years kept, up to the CFPB
  watermark (the last day the weekly run has loaded).
- Each range needs both dates, from <= to, and no date after today.
- The watermarks are never moved, the model is never retrained (`retrain_model` is refused),
  and in prod a backfill is refused on a Monday (Central time), the weekly run's day.
- Only one run of the DAG at a time (max_active_runs=1), so a backfill never runs alongside the
  weekly run; in prod `check_environment` applies the one-scheduler guard as on every run.

`basic_problems` needs only the standard library, so `scripts/backfill.py` can check the dates
before triggering; `plan` adds the checks that need the watermarks and the download modules.
"""

from __future__ import annotations

from datetime import date

FIELDS = {
    "fdic": ("backfill_fdic_from", "backfill_fdic_to"),
    "cfpb": ("backfill_cfpb_from", "backfill_cfpb_to"),
}
LABELS = {"fdic": "FDIC quarters", "cfpb": "CFPB complaints"}
MONDAY = 0
AIRFLOW_BACKFILL = "backfill"  # DagRunType.BACKFILL_JOB


class BackfillRefused(ValueError):
    """The dates or options can't be used for a backfill. The message lists every reason."""


def parse(value, name: str) -> date | None:
    """None, '' -> None; a date or 'YYYY-MM-DD' -> date; anything else is refused."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise BackfillRefused(f"{name}: {value!r} is not a date (use YYYY-MM-DD)") from None


def ranges(params: dict) -> dict[str, tuple[date | None, date | None]]:
    """{source: (from, to)} for each source with at least one date filled in."""
    out = {}
    for source, (a, b) in FIELDS.items():
        start, end = parse(params.get(a), a), parse(params.get(b), b)
        if start or end:
            out[source] = (start, end)
    return out


def basic_problems(params: dict, today: date) -> list[str]:
    """Checks that need nothing but the dates: both ends, order, not in the future."""
    problems = []
    for source, (start, end) in ranges(params).items():
        label = LABELS[source]
        if not (start and end):
            problems.append(f"{label}: give both a from and a to date")
            continue
        if start > end:
            problems.append(f"{label}: from {start} is after to {end}")
        if end > today:
            problems.append(f"{label}: to {end} is in the future (today is {today})")
    return problems


def is_backfill(params: dict) -> bool:
    return bool(ranges(params))


def plan(
    params: dict,
    today: date,
    fdic_watermark: date | None,
    cfpb_watermark: date | None,
    is_prod: bool,
    run_type: str = "manual",
) -> dict:
    """What this run downloads. A scheduled or plain manual run (no dates): {"mode": "weekly"}.
    A backfill: {"mode": "backfill", "fdic_quarters": [...], "cfpb_from", "cfpb_to", and the
    watermarks it leaves alone}. Raises BackfillRefused with every problem found.

    `run_type` "backfill" is Airflow's own Backfill (the Trigger dialog's second choice): it re-runs
    plain weekly runs for past dates, which redoes nothing and uses the Databricks quota, so it
    is refused with a pointer to the right way."""
    from ingestion import cfpb, fdic

    if run_type == AIRFLOW_BACKFILL:
        raise BackfillRefused(
            "Airflow's own Backfill is not used here: it repeats plain weekly runs. To redo a period, "
            "Trigger -> Single Run, and fill in the backfill dates (docs/runbooks/backfill.md)"
        )
    chosen = ranges(params)
    if not chosen:
        return {"mode": "weekly"}
    problems = basic_problems(params, today)
    if str(params.get("retrain_model", False)).strip().lower() in ("true", "1", "yes"):
        problems.append("retrain_model: a backfill never retrains; run it without retrain_model")
    if is_prod and today.weekday() == MONDAY:
        problems.append("prod: no backfills on a Monday, the weekly run's day; start it Tuesday to Saturday")

    quarters: list[date] = []
    if "fdic" in chosen and all(chosen["fdic"]):
        start, end = chosen["fdic"]
        quarters = fdic.quarter_ends(start, end)
        if not quarters:
            problems.append(
                f"FDIC quarters: no quarter-end (Mar 31, Jun 30, Sep 30, Dec 31) between {start} and {end}"
            )
        if start < fdic.HISTORY_START:
            problems.append(f"FDIC quarters: from {start} is before the history start {fdic.HISTORY_START}")
        if fdic_watermark is None:
            problems.append("FDIC quarters: no FDIC watermark yet; let the weekly run load the data first")
        elif quarters and quarters[-1] > fdic_watermark:
            problems.append(
                f"FDIC quarters: {quarters[-1]} is newer than the newest quarter loaded ({fdic_watermark}); "
                "new quarters come with the weekly run"
            )
    cfpb_from = cfpb_to = None
    if "cfpb" in chosen and all(chosen["cfpb"]):
        cfpb_from, cfpb_to = chosen["cfpb"]
        first = cfpb.history_start(today)
        if cfpb_from < first:
            problems.append(
                f"CFPB complaints: from {cfpb_from} is before {first} (the pipeline keeps 3 years)"
            )
        if cfpb_watermark is None:
            problems.append("CFPB complaints: no CFPB watermark yet; let the weekly run load the data first")
        elif cfpb_to > cfpb_watermark:
            problems.append(
                f"CFPB complaints: to {cfpb_to} is after the last day loaded ({cfpb_watermark}); "
                "newer days come with the weekly run"
            )
    if problems:
        raise BackfillRefused("Backfill refused: " + "; ".join(problems))
    return {
        "mode": "backfill",
        "fdic_quarters": [q.isoformat() for q in quarters],
        "cfpb_from": cfpb_from.isoformat() if cfpb_from else None,
        "cfpb_to": cfpb_to.isoformat() if cfpb_to else None,
        "fdic_watermark": fdic_watermark.isoformat() if fdic_watermark else None,
        "cfpb_watermark": cfpb_watermark.isoformat() if cfpb_watermark else None,
    }


def describe(p: dict) -> str:
    """One line for logs and the success message."""
    if p.get("mode") != "backfill":
        return "Weekly run"
    parts = []
    if p.get("fdic_quarters"):
        q = p["fdic_quarters"]
        parts.append(f"FDIC {q[0]}..{q[-1]} ({len(q)} quarter{'s' if len(q) != 1 else ''})")
    if p.get("cfpb_from"):
        parts.append(f"CFPB {p['cfpb_from']}..{p['cfpb_to']}")
    return "Backfill: " + ", ".join(parts) + "; watermarks not moved, no retrain"
