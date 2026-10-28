"""Volume checks: compare this run's row counts with recent successful runs.

Baseline: the median of the last 4 successful runs in OPS.PIPELINE_RUNS (a median, so one odd
week doesn't move it). With fewer than 3 earlier runs there is no baseline: the check passes
and says so.

Thresholds, and why:
- Cumulative layers (BRONZE, SILVER, GOLD, MARTS): these only grow. A new FDIC quarter adds
  about 0.6% to the bank-quarter tables; a week of CFPB complaints adds about 1-2% to the
  complaint tables. So: a drop of more than 0.5% is an alert (a little room for de-duplicated
  amendments), and a jump of more than 5% is an alert (a backfill, a duplicate load, or a
  broken filter).
- SCORED_BANKS (the watchlist): one row per active bank, about 4,250. It changes by closures,
  mergers and new charters, well under 1% a week. More than 3% either way is an alert.
Review these against real PIPELINE_RUNS history after a few weeks (docs/runbooks/alerts.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class Rule:
    max_drop: float
    max_jump: float


RULES = {
    "BRONZE_ROWS": Rule(max_drop=0.005, max_jump=0.05),
    "SILVER_ROWS": Rule(max_drop=0.005, max_jump=0.05),
    "GOLD_ROWS": Rule(max_drop=0.005, max_jump=0.05),
    "MARTS_ROWS": Rule(max_drop=0.005, max_jump=0.05),
    "SCORED_BANKS": Rule(max_drop=0.03, max_jump=0.03),
}
BASELINE_RUNS = 4
MIN_BASELINE_RUNS = 3


def fetch_history(cur, exclude_run_id: str, limit: int = BASELINE_RUNS) -> list[dict]:
    cols = list(RULES)
    cur.execute(
        f"select {', '.join(cols)} from OPS.PIPELINE_RUNS "
        "where STATE = 'success' and RUN_ID <> %(run_id)s order by STARTED_AT desc limit %(limit)s",
        {"run_id": exclude_run_id, "limit": limit},
    )
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def baseline(history: list[dict]) -> dict[str, float]:
    """Median per metric over the runs that have a value for it (needs MIN_BASELINE_RUNS)."""
    out = {}
    for metric in RULES:
        values = [h[metric] for h in history if h.get(metric) is not None]
        if len(values) >= MIN_BASELINE_RUNS:
            out[metric] = float(median(values))
    return out


def compare(current: dict, history: list[dict]) -> dict:
    """{"anomalies": [...], "baseline": {...}, "note": str}. An anomaly names the metric, the
    value, the baseline, the change and the threshold it crossed."""
    base = baseline(history)
    if not base:
        return {
            "anomalies": [],
            "baseline": {},
            "note": f"no baseline yet ({len(history)} earlier successful runs, need {MIN_BASELINE_RUNS})",
        }
    anomalies = []
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
    return {"anomalies": anomalies, "baseline": base, "note": f"compared with {len(history)} earlier runs"}


def describe(anomaly: dict) -> str:
    return (
        f"{anomaly['metric']}: {anomaly['value']:,} vs median {anomaly['baseline_median']:,.0f} "
        f"({anomaly['change_pct']:+.2f}%, limit {anomaly['limit_pct']:+.2f}%)"
    )
