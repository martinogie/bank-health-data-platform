"""Export site/data/watchlist.json from Snowflake MARTS for the watchlist page.

Logs in as the key-pair service user configured in .env (SNOWFLAKE_USER,
SNOWFLAKE_PRIVATE_KEY_PATH). Prints only a short summary: no secrets, no account identifier.
The Airflow DAG runs the same export as its final task (export_watchlist).

Usage (from the project folder):
    .venv\\Scripts\\python scripts/export_watchlist_data.py [--out site/data/watchlist.json]
"""

from __future__ import annotations

import argparse
import logging
import sys

from _env import ROOT, load_env

sys.path.insert(0, str(ROOT))
from ingestion import watchlist_export  # noqa: E402
from ingestion.snowflake_load import connect_from_env  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "site" / "data" / "watchlist.json"))
    args = parser.parse_args()

    # The Snowflake connector logs its login URL (which contains the account) at INFO.
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("snowflake").setLevel(logging.ERROR)

    load_env()
    conn = connect_from_env()
    try:
        summary = watchlist_export.export(conn, args.out)
    finally:
        conn.close()
    print(
        f"wrote {summary['path']}: {summary['bytes']:,} bytes, {summary['banks']:,} banks, "
        f"data quarter {summary['quarter']}, model v{summary['model_version']}"
    )


if __name__ == "__main__":
    main()
