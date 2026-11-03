"""Model monitoring (M-08): 07_monitor's summary -> OPS.MODEL_MONITORING, and the alert.

07_monitor (Databricks) computes PSI for the score and each feature (vs the previous 4 quarters,
and vs 2001-2019 training) and a backtest (realised PR-AUC and top-10% capture per quarter from
2020), plus the list of what would alert (rules in databricks/notebooks/_monitoring.py).

check_model (Airflow) writes one row per metric to OPS.MODEL_MONITORING and, when alerts are
switched on (MODEL_MONITOR_ALERTS=on), sends a "MODEL" message to the alert channels. It never
fails the run: drift doesn't make this week's scores wrong, and the watchlist still goes out.
Switched on in prod (docker-compose.yaml) by Martins on 3 Nov 2026, after reviewing both PSI
comparisons in dev; the code default is off.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

TABLE = "OPS.MODEL_MONITORING"
COLUMNS = (
    "RUN_ID", "ENVIRONMENT", "LATEST_QUARTER", "MODEL", "KIND", "REFERENCE", "NAME", "REPORT_DATE",
    "VALUE", "LEVEL", "ROWS_JUDGED", "POSITIVES", "JUDGED",
)  # fmt: skip


def alerts_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in ("on", "true", "1", "yes")


def rows(summary: dict[str, Any], run_id: str, environment: str) -> list[dict[str, Any]]:
    """One row per PSI value and per backtest metric."""
    base = {
        "RUN_ID": run_id,
        "ENVIRONMENT": environment,
        "LATEST_QUARTER": summary.get("latest_quarter"),
        "MODEL": summary.get("model"),
    }
    out = []
    for p in summary.get("psi") or []:
        out.append(
            {
                **dict.fromkeys(COLUMNS),
                **base,
                "KIND": "psi",
                "REFERENCE": p["reference"],
                "NAME": p["name"],
                "VALUE": p["psi"],
                "LEVEL": p["level"],
            }
        )
    for b in summary.get("backtest") or []:
        for metric in ("pr_auc", "top10_capture"):
            out.append(
                {
                    **dict.fromkeys(COLUMNS),
                    **base,
                    "KIND": "backtest",
                    "NAME": metric,
                    "REPORT_DATE": b["report_date"],
                    "VALUE": b[metric],
                    "ROWS_JUDGED": b["rows"],
                    "POSITIVES": b["positives"],
                    "JUDGED": b["judged"],
                }
            )
    return out


def table_exists(cur) -> bool:
    cur.execute(
        "select count(*) from INFORMATION_SCHEMA.TABLES where TABLE_SCHEMA = 'OPS' and TABLE_NAME = 'MODEL_MONITORING'"
    )
    return bool(cur.fetchone()[0])


def write(cur, run_id: str, records: list[dict[str, Any]]) -> int:
    """Replace this run's rows (a retry writes the same rows again, never twice)."""
    cur.execute(f"delete from {TABLE} where RUN_ID = %(r)s", {"r": run_id})
    if records:
        placeholders = ", ".join(f"%({c})s" for c in COLUMNS)
        cur.executemany(f"insert into {TABLE} ({', '.join(COLUMNS)}) values ({placeholders})", records)
    return len(records)


def headline(summary: dict[str, Any]) -> str:
    """One line for logs and the OK message: score PSI both ways, large feature shifts, latest backtest."""
    psi = {(p["reference"], p["name"]): p for p in summary.get("psi") or []}
    recent, train = psi.get(("previous_4q", "score")), psi.get(("training_2001_2019", "score"))
    large = sum(
        1
        for (ref, name), p in psi.items()
        if ref == "previous_4q" and name != "score" and p["level"] == "large"
    )
    judged = [b for b in summary.get("backtest") or [] if b["judged"]]
    parts = [
        f"score PSI {recent['psi']:.3f} vs previous 4 quarters"
        if recent and recent["psi"] is not None
        else None,
        f"{train['psi']:.3f} vs 2001-2019" if train and train["psi"] is not None else None,
        f"{large} feature(s) with a large shift",
    ]
    if judged:
        b = judged[-1]
        parts.append(f"backtest {b['report_date']}: top-10% capture {b['top10_capture']:.1%}")
    return "Model monitoring: " + ", ".join(p for p in parts if p)
