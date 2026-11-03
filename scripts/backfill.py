r"""Redo a past period: trigger a backfill run of bank_health_weekly from the command line.

The same as Trigger in the Airflow UI with the backfill dates filled in (docs/runbooks/backfill.md).
Checks the dates first (both ends, from <= to, nothing in the future); the run's own first steps
then check them against the watermarks before anything is downloaded.

Usage (from the project folder, on the machine whose Airflow should run it):
    python scripts/backfill.py --fdic 2026-03-31:2026-06-30
    python scripts/backfill.py --cfpb 2026-09-01:2026-09-30
    python scripts/backfill.py --fdic 2026-06-30:2026-06-30 --cfpb 2026-09-01:2026-09-30 --dry-run

On the PC: .venv\Scripts\python scripts/backfill.py ...   On the VM: python3 scripts/backfill.py ...
It runs `docker compose exec airflow-scheduler airflow dags trigger` in airflow/, so it needs the
Airflow stack running there. No secrets are read or printed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ingestion import backfill  # noqa: E402  (standard library only at import)

DAG_ID = "bank_health_weekly"


def date_range(text: str) -> tuple[str, str]:
    """'2026-03-31:2026-06-30' -> ('2026-03-31', '2026-06-30'); one date means from = to."""
    parts = text.split(":")
    if len(parts) == 1:
        parts = parts * 2
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"{text!r}: use FROM:TO, e.g. 2026-03-31:2026-06-30")
    for p in parts:
        try:
            date.fromisoformat(p)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{p!r} is not a date (use YYYY-MM-DD)") from None
    return parts[0], parts[1]


def build_conf(fdic: tuple[str, str] | None, cfpb: tuple[str, str] | None) -> dict:
    conf: dict = {}
    for source, chosen in (("fdic", fdic), ("cfpb", cfpb)):
        if chosen:
            start_field, end_field = backfill.FIELDS[source]
            conf[start_field], conf[end_field] = chosen
    return conf


def trigger_command(conf: dict) -> list[str]:
    return [
        "docker", "compose", "exec", "-T", "airflow-scheduler",
        "airflow", "dags", "trigger", DAG_ID, "--conf", json.dumps(conf),
    ]  # fmt: skip


def main(argv: list[str] | None = None, today: date | None = None, run=subprocess.run) -> int:
    parser = argparse.ArgumentParser(description="Trigger a backfill run of bank_health_weekly.")
    parser.add_argument(
        "--fdic", type=date_range, help="FDIC quarter-ends FROM:TO, e.g. 2026-03-31:2026-06-30"
    )
    parser.add_argument(
        "--cfpb", type=date_range, help="CFPB date received FROM:TO, e.g. 2026-09-01:2026-09-30"
    )
    parser.add_argument("--dry-run", action="store_true", help="check and print the command; trigger nothing")
    args = parser.parse_args(argv)
    if not (args.fdic or args.cfpb):
        parser.error("give --fdic and/or --cfpb")
    conf = build_conf(args.fdic, args.cfpb)
    problems = backfill.basic_problems(conf, today or date.today())
    if problems:
        print("Backfill refused: " + "; ".join(problems), file=sys.stderr)
        return 2
    cmd = trigger_command(conf)
    print("Backfill:", json.dumps(conf))
    print("Command (in airflow/):", " ".join(cmd[:-1]), f"'{cmd[-1]}'")
    if args.dry_run:
        print("Dry run: nothing triggered.")
        return 0
    result = run(cmd, cwd=ROOT / "airflow")
    if result.returncode != 0:
        print("Trigger failed: is the Airflow stack running in airflow/?", file=sys.stderr)
        return result.returncode
    print("Triggered. Watch it in the Airflow UI (bank_health_weekly, the newest manual run);")
    print("its check_backfill step checks the dates against the watermarks first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
