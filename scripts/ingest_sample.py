"""Download a small slice of real data and upload it to the Databricks Volume.

For testing the notebooks before the full Airflow run. Uses the same ingestion
code as the DAG and the same file names, so the full run later simply
overwrites these files. Airflow's watermarks are not touched.

Usage (from the project folder):
    .venv\\Scripts\\python scripts/ingest_sample.py --quarters 2 --cfpb-month 2026-09
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

from _env import ROOT, load_env

sys.path.insert(0, str(ROOT))
from ingestion import cfpb, fdic  # noqa: E402
from ingestion.databricks_files import DatabricksFiles  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quarters", type=int, default=2, help="latest N FDIC quarters")
    parser.add_argument("--cfpb-month", default=None, help="YYYY-MM of complaints (default: last full month)")
    parser.add_argument("--no-upload", action="store_true", help="download only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_env()
    today = date.today()

    latest = fdic.latest_financials_quarter()
    quarters = fdic.quarter_ends(fdic.HISTORY_START, latest)[-args.quarters:]
    paths = fdic.download_financials(quarters, RAW_DIR / "fdic")
    paths.append(fdic.download_institutions(RAW_DIR / "fdic", today))
    paths.append(fdic.download_failures(RAW_DIR / "fdic", today))

    if args.cfpb_month:
        start = date.fromisoformat(args.cfpb_month + "-01")
    else:
        start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    month_end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    complaints = cfpb.download_window(start, month_end, RAW_DIR / "cfpb")
    if complaints:
        paths.append(complaints)

    if args.no_upload:
        return
    uploaded = DatabricksFiles.from_env().upload_files(paths, RAW_DIR)
    print(f"\nUploaded {len(uploaded)} files:")
    for p in uploaded:
        print("  ", p)


if __name__ == "__main__":
    main()
