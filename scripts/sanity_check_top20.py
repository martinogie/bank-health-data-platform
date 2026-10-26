"""Check the 20 riskiest scored banks before release (see ingestion/sanity_checks.py).

Fails (exit 1) on a new bank, a foreign-bank office, a 0% or missing Tier 1 leverage ratio, or
a reason quoting an unusable capital ratio. Prints the top 20 either way. No secrets printed.

Usage (from the project folder):
    .venv\\Scripts\\python scripts/sanity_check_top20.py                       # Snowflake RAW.ML_RISK_SCORES
    .venv\\Scripts\\python scripts/sanity_check_top20.py --databricks ml.risk_scores_candidate
"""

from __future__ import annotations

import argparse
import logging
import sys

from _env import ROOT, load_env

sys.path.insert(0, str(ROOT))
from ingestion import sanity_checks  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--databricks",
        metavar="SCHEMA.TABLE",
        help="check a Databricks table instead, e.g. ml.risk_scores_candidate",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("snowflake").setLevel(logging.ERROR)  # login URL contains the account
    load_env()

    if args.databricks:
        from mirror_to_postgres import DatabricksSource  # same SQL-warehouse connection as the mirror

        source = DatabricksSource()
        conn = source.conn
        table = f"{source.catalog}.{args.databricks}"
    else:
        from ingestion.snowflake_load import connect_from_env

        conn = connect_from_env()
        table = "RAW.ML_RISK_SCORES"
    try:
        cur = conn.cursor()
        try:
            print(sanity_checks.run(cur, table))
        except ValueError as exc:
            print(exc)
            raise SystemExit(1) from None
        finally:
            cur.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
