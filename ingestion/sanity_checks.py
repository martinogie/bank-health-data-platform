"""Release guardrail: the 20 riskiest scored banks must be ones the model can judge.

Fails if any of the top 20 (by distress probability, scored bands only) is a new bank, an office
of a foreign bank, has a 0% (or missing) Tier 1 leverage ratio, or has a reason quoting a capital
ratio of 0.00 or total risk-based capital (not a model feature since v5). These were the
problems found in the v4 review (docs/investigations/model_credibility.md).

Used by the Airflow task `sanity_check_top20` (after scoring, before dbt) and by
scripts/sanity_check_top20.py for a manual check, e.g. of a candidate model before promotion.
"""
from __future__ import annotations

import re

TOP_N = 20
NEW_BANK_YEARS = 3.0
SCORED_BANDS = ("High", "Elevated", "Watch", "Low")
COLUMNS = ("cert", "bank_name", "state_code", "bank_age_years", "distress_probability", "risk_band",
           "is_new_bank", "is_foreign_office", "tier1_leverage_pct", "reason_1", "reason_2", "reason_3")
_ZERO_CAPITAL_REASON = re.compile(r"(tier1_leverage_pct|total_risk_based_capital_pct)\s*=\s*-?0(\.0+)?\s*\(")


def top_sql(table: str, n: int = TOP_N) -> str:
    bands = ", ".join(f"'{b}'" for b in SCORED_BANDS)
    return (f"select {', '.join(COLUMNS)} from {table} "
            f"where risk_band in ({bands}) order by distress_probability desc, cert limit {int(n)}")


def problems_in_top(rows: list[dict]) -> list[str]:
    """Plain-language problems, one per bank and rule; empty list = pass."""
    out = []
    for r in rows:
        who = f"CERT {r['cert']} {r.get('bank_name') or ''}".strip()
        age = r.get("bank_age_years")
        if r.get("is_new_bank") or (age is not None and age < NEW_BANK_YEARS):
            out.append(f"{who}: new bank ({age} years old) in the top {TOP_N}")
        if r.get("is_foreign_office"):
            out.append(f"{who}: office of a foreign bank in the top {TOP_N}")
        t1 = r.get("tier1_leverage_pct")
        if t1 is None:
            out.append(f"{who}: Tier 1 leverage not reported")
        elif t1 == 0:
            out.append(f"{who}: Tier 1 leverage is 0%")
        for i in (1, 2, 3):
            reason = r.get(f"reason_{i}") or ""
            if _ZERO_CAPITAL_REASON.search(reason) or "total_risk_based" in reason:
                out.append(f"{who}: reason_{i} quotes a capital ratio that isn't usable: {reason}")
    return out


def format_table(rows: list[dict]) -> str:
    lines = [f"{'#':>2}  {'CERT':>6}  {'bank':<40} {'st':<2} {'age':>5} {'prob':>6}  {'band':<8} {'T1 %':>6}  top reason"]
    for i, r in enumerate(rows, 1):
        age = r.get("bank_age_years")
        t1 = r.get("tier1_leverage_pct")
        lines.append(f"{i:>2}  {r['cert']:>6}  {str(r.get('bank_name'))[:40]:<40} {r.get('state_code') or '':<2} "
                     f"{'' if age is None else f'{age:.1f}':>5} {r['distress_probability']:>6.3f}  "
                     f"{r['risk_band']:<8} {'' if t1 is None else f'{t1:.2f}':>6}  {r.get('reason_1') or ''}")
    return "\n".join(lines)


def run(cursor, table: str) -> dict:
    """Query the top 20 with a DB-API cursor; raise ValueError listing every problem."""
    cursor.execute(top_sql(table))
    cols = [d[0].lower() for d in cursor.description]
    rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    if len(rows) < TOP_N:
        raise ValueError(f"only {len(rows)} scored banks in {table}; expected at least {TOP_N}")
    print(format_table(rows))
    problems = problems_in_top(rows)
    if problems:
        raise ValueError(f"top-{TOP_N} sanity check failed ({len(problems)} problems):\n  " + "\n  ".join(problems))
    return {"table": table, "checked": len(rows), "problems": 0, "top_cert": rows[0]["cert"]}
