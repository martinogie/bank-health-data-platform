r"""Snapshots for the D-02 acceptance runs: take one after a pipeline run, then compare two.

    .venv\Scripts\python scripts/acceptance_snapshot.py take week_a
    .venv\Scripts\python scripts/acceptance_snapshot.py take week_b
    .venv\Scripts\python scripts/acceptance_snapshot.py compare week_a week_b

`take` reads every layer, read-only, in the environment set by BANK_HEALTH_ENV (.env):
  - Databricks bronze, silver, gold, risk scores (one serverless run of
    tools/acceptance_checksums; uses a little of the Free Edition daily quota);
  - Snowflake RAW and MARTS (one HASH_AGG query per table on the environment's warehouse).
  --snowflake-only skips Databricks. --marts-schema MARTS_FULLCHECK reads the marts from another
  schema (e.g. a full-refresh build to compare with the incremental MARTS).
Snapshots are saved in data/acceptance/<name>.json (git-ignored). `compare` exits 1 if any table
differs (bronze on distinct keys only; ingestion/checksums.py); `--only SNOWFLAKE.MARTS` limits it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from _env import ROOT, load_env

sys.path.insert(0, str(ROOT))
from ingestion import checksums  # noqa: E402

OUT = ROOT / "data" / "acceptance"


def databricks_snapshot() -> dict:
    from databricks_run import run_notebook

    from ingestion import databricks_jobs as dj
    from ingestion import environment

    s = environment.settings()
    db = dj.DatabricksAPI.from_env()
    repo_path = dj.sync_notebooks(db, s)
    outputs: dict = {}
    params = {"catalog": environment.databricks_catalog(), "env": s.name}
    if not run_notebook(db, repo_path, "tools/acceptance_checksums", params, outputs=outputs):
        raise SystemExit("tools/acceptance_checksums failed")
    return {f"DATABRICKS.{t}": v for t, v in outputs["tools/acceptance_checksums"]["tables"].items()}


def snowflake_snapshot(marts_schema: str | None) -> dict:
    import os

    from ingestion.snowflake_load import connect_from_env

    tables = dict(checksums.SNOWFLAKE_TABLES)
    if marts_schema:
        tables = {t.replace("MARTS.", f"{marts_schema}.", 1): k for t, k in tables.items()}
    conn = connect_from_env()
    try:
        out = checksums.snowflake_checksums(conn.cursor(), os.environ["SNOWFLAKE_DATABASE"], tables)
    finally:
        conn.close()
    if marts_schema:  # name them MARTS.*, so they line up with a normal snapshot
        out = {k.replace(f".{marts_schema}.", ".MARTS.", 1): v for k, v in out.items()}
    return out


def take(name: str, snowflake_only: bool, marts_schema: str | None) -> None:
    load_env()
    from ingestion import environment

    snap = {
        "name": name,
        "environment": environment.settings().name,
        "taken_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "marts_schema": marts_schema or "MARTS",
        "tables": {},
    }
    if not snowflake_only:
        snap["tables"].update(databricks_snapshot())
    snap["tables"].update(snowflake_snapshot(marts_schema))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    print(f"saved {path} ({len(snap['tables'])} tables)")


def compare(a_name: str, b_name: str, only: str | None = None) -> int:
    a, b = (json.loads((OUT / f"{n}.json").read_text(encoding="utf-8")) for n in (a_name, b_name))
    ta, tb = a["tables"], b["tables"]
    if only:  # e.g. SNOWFLAKE.MARTS: compare just those tables
        ta, tb = ({k: v for k, v in t.items() if k.startswith(only)} for t in (ta, tb))
    print(
        f"{a_name} ({a['taken_at']}, {a['marts_schema']}) vs {b_name} ({b['taken_at']}, {b['marts_schema']})"
    )
    for line in checksums.summary_lines(ta, tb):
        print(" ", line)
    diff = checksums.differences(ta, tb)
    print("IDENTICAL" if not diff else f"DIFFERENT: {len(diff)} table(s)")
    return 1 if diff else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("take")
    t.add_argument("name")
    t.add_argument("--snowflake-only", action="store_true")
    t.add_argument("--marts-schema")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--only", help="compare only tables starting with this, e.g. SNOWFLAKE.MARTS")
    args = parser.parse_args()
    if args.cmd == "take":
        take(args.name, args.snowflake_only, args.marts_schema)
        return 0
    return compare(args.a, args.b, args.only)


if __name__ == "__main__":
    sys.exit(main())
