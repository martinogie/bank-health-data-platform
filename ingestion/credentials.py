"""Credential expiry and rotation checks (O-05).

Two sources, neither holding a secret:
- **Databricks tokens:** their expiry dates come from the token API (`/api/2.0/token/list`, the
  tokens of the user the pipeline's token belongs to: the pipeline's own token and the GitHub
  bundle-deploy token).
- **`ops/credentials.yml`:** everything else, by date: Snowflake key pairs (they don't expire, so
  each has a rotate-by date: set on + 180 days), and dated reminders (e.g. removing the owner's home
  IP from the network policy on 20 Nov 2026).

`due(...)` lists what is due within WARN_DAYS (14) or already overdue. The DAG's `check_credentials`
task sends one EXPIRING message for them and never fails the run: an expiring credential is a
reason to rotate this week, not to stop the pipeline.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

WARN_DAYS = 14
DEFAULT_ROTATE_DAYS = 180


def load(path: Path) -> list[dict[str, Any]]:
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return list(data.get("credentials") or [])


def _as_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def deadline(entry: dict[str, Any]) -> date | None:
    """The date by which to act: `due` if given, else `set_on` + `rotate_days` (default 180)."""
    if entry.get("due"):
        return _as_date(entry["due"])
    set_on = _as_date(entry.get("set_on"))
    if set_on is None:
        return None
    return set_on + timedelta(days=int(entry.get("rotate_days") or DEFAULT_ROTATE_DAYS))


def token_deadlines(token_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Databricks token_list entries -> {name, deadline}; tokens without an expiry are skipped
    (expiry_time is -1 or 0)."""
    out = []
    for t in token_infos:
        ms = t.get("expiry_time") or 0
        if ms <= 0:
            continue
        out.append(
            {
                "name": f"Databricks token '{t.get('comment') or t.get('token_id', '?')}'",
                "deadline": datetime.fromtimestamp(ms / 1000, tz=UTC).date(),
                "how": "docs/runbooks/rotation.md#databricks-tokens",
            }
        )
    return out


def due(
    entries: list[dict[str, Any]], tokens: list[dict[str, Any]], today: date, warn_days: int = WARN_DAYS
) -> list[str]:
    """One line per item due within warn_days or overdue, soonest first. Entries without a date
    are reported once each, so a missing date can't hide an item."""
    items = [{"name": e["name"], "deadline": deadline(e), "how": e.get("how", "")} for e in entries] + tokens
    lines = []
    for i in sorted(items, key=lambda i: (i["deadline"] is not None, i["deadline"] or today)):
        d = i["deadline"]
        if d is None:
            lines.append(f"{i['name']}: no date recorded in ops/credentials.yml")
            continue
        days = (d - today).days
        if days > warn_days:
            continue
        when = f"{-days} day(s) overdue" if days < 0 else "today" if days == 0 else f"in {days} day(s)"
        lines.append(
            f"{i['name']}: due {d.isoformat()} ({when})" + (f"; how: {i['how']}" if i["how"] else "")
        )
    return lines
