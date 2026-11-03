"""Volume checks: compare this run's row counts with recent successful runs.

Two kinds of check:

1. Rows ADDED to bronze, per source (CFPB complaints, FDIC financials, institutions, failures).
   Bronze is append-only and every run re-downloads part of each source (30 days of CFPB, the last
   2 FDIC quarters, the full institutions and failures lists), so its TOTAL grows on every run by
   design. What a broken or doubled download changes is how many rows a run adds:
   - added = 0, or under 50% of the usual: a broken or partial download;
   - added over 3x the usual: a full re-download or a duplicated load.
   "Usual" = the median rows added per run over the last 4 successful runs (needs 3). When a new
   FDIC quarter arrives, FDIC financials may add more (one more quarter file), so their upper limit
   is not applied in that run. The total-bronze rule this replaces fired on 2 Nov 2026 on normal
   growth (each run adds ~90k rows; the median of 4 runs sits ~2.5 runs back).
2. Totals of the other layers (SILVER, GOLD, MARTS) and SCORED_BANKS against the median of the
   last 4 successful runs:
   - SILVER, GOLD, MARTS only grow. A new FDIC quarter adds about 0.6% to the bank-quarter tables;
     a week of CFPB complaints adds about 1-2% to the complaint tables. A drop of more than 0.5%
     or a jump of more than 5% is an alert.
   - SCORED_BANKS (the watchlist): about 4,250 active banks, changing well under 1% a week. More
     than 3% either way is an alert.

Rows added per source are stored in the run log (OPS.PIPELINE_RUNS.ROWS_DETAIL:bronze_added, from
the bronze notebook's own count). For runs recorded before that, they are worked out from the
change in each source's bronze total since the previous successful run.

3. The complaint match rate: the share of complaints matched to an FDIC bank (silver; stored in
   ROWS_DETAIL:match). It moves by a few tenths of a point a week; a drop of more than
   MATCH_RATE_MAX_DROP_PTS percentage points below the median of the last 4 successful runs (needs
   3) means matching broke (an empty or changed institutions file, a normalisation change), and
   is an alert. Checked in backfills too.

Backfill runs (ingestion/backfill.py; ROWS_DETAIL:backfill is set) add far more rows than usual by
design, so in a backfill the rows-added check only reports, and every other check still applies.
Backfill runs are also left out of the history, so they never shift the usual rows added or the
medians that the weekly runs are judged against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from statistics import median
from typing import Any


@dataclass(frozen=True)
class Rule:
    max_drop: float
    max_jump: float


RULES = {
    "SILVER_ROWS": Rule(max_drop=0.005, max_jump=0.05),
    "GOLD_ROWS": Rule(max_drop=0.005, max_jump=0.05),
    "MARTS_ROWS": Rule(max_drop=0.005, max_jump=0.05),
    "SCORED_BANKS": Rule(max_drop=0.03, max_jump=0.03),
}
BASELINE_RUNS = 4
MIN_BASELINE_RUNS = 3

BRONZE_SOURCES = ("cfpb_complaints", "fdic_financials", "fdic_institutions", "fdic_failures")
ADDED_MIN_RATIO = 0.5  # under half the usual rows added: a broken or partial download
ADDED_MAX_RATIO = 3.0  # over three times the usual: a full re-download or a duplicated load
QUARTER_STEP_SOURCE = "fdic_financials"  # may add a whole new quarter when one arrives
MATCH_RATE = "complaints_matched_to_bank_pct"
MATCH_RATE_MAX_DROP_PTS = 2.0  # percentage points below the usual (about 30% today)


def _detail(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            out = json.loads(value)
            return out if isinstance(out, dict) else {}
        except ValueError:
            return {}
    return {}


def fetch_history(cur, exclude_run_id: str, limit: int = BASELINE_RUNS) -> list[dict]:
    """The last successful runs, newest first, backfills left out: the RULES metrics plus
    ROWS_DETAIL and the latest FDIC quarter. One extra run is read so that rows added can be
    worked out for `limit` runs."""
    cols = [*RULES, "ROWS_DETAIL", "LATEST_FDIC_QUARTER"]
    cur.execute(
        f"select {', '.join(cols)} from OPS.PIPELINE_RUNS "
        "where STATE = 'success' and RUN_ID <> %(run_id)s and ROWS_DETAIL:backfill is null "
        "order by STARTED_AT desc limit %(limit)s",
        {"run_id": exclude_run_id, "limit": limit + 1},
    )
    rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    for r in rows:
        r["ROWS_DETAIL"] = _detail(r.get("ROWS_DETAIL"))
    return rows


def baseline(history: list[dict]) -> dict[str, float]:
    """Median per metric over the last BASELINE_RUNS runs that have it (needs MIN_BASELINE_RUNS)."""
    out = {}
    for metric in RULES:
        values = [h[metric] for h in history[:BASELINE_RUNS] if h.get(metric) is not None]
        if len(values) >= MIN_BASELINE_RUNS:
            out[metric] = float(median(values))
    return out


def added_in(run: dict, older: dict | None) -> dict[str, int]:
    """Rows added to bronze per source by one run: its stored count, else the change in each
    source's bronze total since the older run (empty if neither is known)."""
    detail = _detail(run.get("ROWS_DETAIL"))
    stored = detail.get("bronze_added")
    if isinstance(stored, dict) and stored:
        return {k: int(v) for k, v in stored.items() if v is not None}
    now = detail.get("bronze") or {}
    before = _detail((older or {}).get("ROWS_DETAIL")).get("bronze") or {}
    return {
        k: int(now[k]) - int(before[k])
        for k in now
        if k in before and now[k] is not None and before[k] is not None
    }


def usual_added(history: list[dict]) -> dict[str, float]:
    """Median rows added per source over the last BASELINE_RUNS runs that have it (needs 3)."""
    per_run = [
        added_in(run, history[i + 1] if i + 1 < len(history) else None) for i, run in enumerate(history)
    ]
    out = {}
    for source in BRONZE_SOURCES:
        values = [a[source] for a in per_run[:BASELINE_RUNS] if source in a]
        if len(values) >= MIN_BASELINE_RUNS:
            out[source] = float(median(values))
    return out


def match_rate(run: dict) -> float | None:
    value = (_detail(run.get("ROWS_DETAIL")).get("match") or {}).get(MATCH_RATE)
    return float(value) if value is not None else None


def usual_match_rate(history: list[dict]) -> float | None:
    """Median match rate over the last BASELINE_RUNS runs that have it (needs MIN_BASELINE_RUNS)."""
    values = [v for v in (match_rate(h) for h in history[:BASELINE_RUNS]) if v is not None]
    return float(median(values)) if len(values) >= MIN_BASELINE_RUNS else None


def check_match_rate(current: dict, history: list[dict]) -> list[dict]:
    value, usual = match_rate(current), usual_match_rate(history)
    if value is None or usual is None or value >= usual - MATCH_RATE_MAX_DROP_PTS:
        return []
    return [
        {
            "metric": "MATCH_RATE",
            "value": value,
            "usual_match_rate": usual,
            "drop_pts": round(usual - value, 1),
            "limit_pts": MATCH_RATE_MAX_DROP_PTS,
        }
    ]


def check_added(current: dict[str, int], usual: dict[str, float], new_quarter: bool = False) -> list[dict]:
    """Anomalies in the rows added to bronze this run, per source."""
    anomalies = []
    for source in BRONZE_SOURCES:
        value, ref = current.get(source), usual.get(source)
        if value is None or not ref:
            continue
        ratio = value / ref
        too_few = value == 0 or ratio < ADDED_MIN_RATIO
        too_many = ratio > ADDED_MAX_RATIO and not (new_quarter and source == QUARTER_STEP_SOURCE)
        if too_few or too_many:
            anomalies.append(
                {
                    "metric": f"BRONZE_ADDED.{source}",
                    "value": value,
                    "usual_added": ref,
                    "ratio": round(ratio, 2),
                    "limit": f"{ADDED_MIN_RATIO:g}x-{ADDED_MAX_RATIO:g}x of usual",
                }
            )
    return anomalies


def compare(
    current: dict,
    history: list[dict],
    current_added: dict[str, int] | None = None,
    new_quarter: bool = False,
    backfill: bool = False,
) -> dict:
    """{"anomalies": [...], "reported": [...], "baseline": {...}, "usual_added": {...},
    "added": {...}, "note": str}. `current_added` = rows added to bronze per source in this run
    (worked out from the bronze totals in current["ROWS_DETAIL"] if missing). An anomaly names the
    metric, the value, its reference and the limit. In a backfill, rows-added findings go to
    "reported" (shown, never failing the run); "anomalies" fail the run."""
    base = baseline(history)
    if not current_added and history:
        current_added = added_in({"ROWS_DETAIL": current.get("ROWS_DETAIL")}, history[0])
    current_added = current_added or {}
    usual = usual_added(history)
    usual_rate = usual_match_rate(history)
    if not base and not usual and usual_rate is None:
        return {
            "anomalies": [],
            "reported": [],
            "baseline": {},
            "usual_added": {},
            "usual_match_rate": None,
            "added": current_added,
            "note": f"no baseline yet ({len(history)} earlier successful runs, need {MIN_BASELINE_RUNS})",
        }
    added_findings = check_added(current_added, usual, new_quarter)
    reported = added_findings if backfill else []
    anomalies = ([] if backfill else added_findings) + check_match_rate(current, history)
    for metric, rule in RULES.items():
        value, ref = current.get(metric), base.get(metric)
        if value is None or not ref:
            continue
        change = (value - ref) / ref
        if change < -rule.max_drop or change > rule.max_jump:
            limit = rule.max_drop if change < 0 else rule.max_jump
            anomalies.append(
                {
                    "metric": metric,
                    "value": value,
                    "baseline_median": ref,
                    "change_pct": round(100 * change, 2),
                    "limit_pct": round(100 * (-limit if change < 0 else limit), 2),
                }
            )
    note = (
        f"compared with {min(len(history), BASELINE_RUNS)} earlier runs"
        + ("; new FDIC quarter: no upper limit on FDIC financials added" if new_quarter else "")
        + ("; backfill: rows added are reported, not checked" if backfill else "")
    )
    return {
        "anomalies": anomalies,
        "reported": reported,
        "baseline": base,
        "usual_added": usual,
        "usual_match_rate": usual_rate,
        "added": current_added,
        "note": note,
    }


def describe(anomaly: dict) -> str:
    if anomaly["metric"] == "MATCH_RATE":
        return (
            f"complaints matched to a bank: {anomaly['value']:.1f}% vs usual {anomaly['usual_match_rate']:.1f}% "
            f"(down {anomaly['drop_pts']:.1f} points; limit {anomaly['limit_pts']:.1f})"
        )
    if "usual_added" in anomaly:
        source = anomaly["metric"].split(".", 1)[1]
        return (
            f"rows added to bronze {source}: {anomaly['value']:,} vs usual {anomaly['usual_added']:,.0f} "
            f"({anomaly['ratio']:g}x; limit {anomaly['limit']})"
        )
    return (
        f"{anomaly['metric']}: {anomaly['value']:,} vs median {anomaly['baseline_median']:,.0f} "
        f"({anomaly['change_pct']:+.2f}%, limit {anomaly['limit_pct']:+.2f}%)"
    )


def is_new_quarter(latest_quarter: Any, history: list[dict]) -> bool:
    """True when this run's latest FDIC quarter is newer than the last successful run's."""
    if not latest_quarter or not history or not history[0].get("LATEST_FDIC_QUARTER"):
        return False
    return str(latest_quarter)[:10] > str(history[0]["LATEST_FDIC_QUARTER"])[:10]
