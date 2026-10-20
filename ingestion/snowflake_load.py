"""Load Databricks exports (Parquet in a UC Volume) into Snowflake BANK_HEALTH.RAW.

Flow per table:
  1. list   /Volumes/bank_health/gold/exports/<folder>/*.parquet
  2. download each part file, write it into RAW.<TABLE>__LOADING (write_pandas)
  3. ALTER TABLE <TABLE> SWAP WITH <TABLE>__LOADING, then drop the loading copy
Step 3 is a single atomic metadata change, so readers never see a half-loaded table.

Auth is key-pair: the private key file path comes from SNOWFLAKE_PRIVATE_KEY_PATH.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

import pandas as pd

from ingestion.databricks_files import DatabricksFiles

log = logging.getLogger(__name__)

EXPORT_ROOT = "/Volumes/bank_health/gold/exports"
RAW_SCHEMA = "RAW"

# Snowflake RAW table  ->  export folder written by the Databricks notebooks
EXPORT_TABLES = {
    "FDIC_FINANCIALS": "fdic_financials",
    "FDIC_INSTITUTIONS": "fdic_institutions",
    "CFPB_COMPLAINTS": "cfpb_complaints",
    "ML_RISK_SCORES": "ml_risk_scores",
}


def connect_from_env():
    """Open a Snowflake connection using the service user's private key."""
    import snowflake.connector  # imported here so unit tests don't need it

    kwargs = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        role=os.environ["SNOWFLAKE_ROLE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=RAW_SCHEMA,
        authenticator="SNOWFLAKE_JWT",
        private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"],
    )
    passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    if passphrase:
        kwargs["private_key_file_pwd"] = passphrase
    return snowflake.connector.connect(**kwargs)


def parquet_parts(dbx: DatabricksFiles, export_dir: str) -> list[str]:
    """Parquet data files in a Spark output folder (skips _SUCCESS, _committed_*, etc.)."""
    parts = [
        e["path"] for e in dbx.list_directory(export_dir)
        if not e.get("is_directory")
        and e["path"].endswith(".parquet")
        and not PurePosixPath(e["path"]).name.startswith(("_", "."))
    ]
    return sorted(parts)


def prepare_frame(df: pd.DataFrame, loaded_at: datetime) -> pd.DataFrame:
    """Upper-case column names (Snowflake convention) and stamp the load time."""
    df = df.copy()
    df.columns = [str(c).upper() for c in df.columns]
    df["_LOADED_AT"] = pd.Timestamp(loaded_at)
    return df


def load_table(
    conn,
    dbx: DatabricksFiles,
    table: str,
    export_dir: str,
    writer: Callable | None = None,
) -> int:
    """Replace RAW.<table> with the contents of `export_dir`. Returns rows loaded."""
    if writer is None:
        from snowflake.connector.pandas_tools import write_pandas as writer

    parts = parquet_parts(dbx, export_dir)
    if not parts:
        raise FileNotFoundError(f"No parquet files found in {export_dir}")

    loading = f"{table}__LOADING"
    loaded_at = datetime.now(timezone.utc)
    total = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, part in enumerate(parts):
            local = dbx.download_file(part, Path(tmp) / f"part_{i}.parquet")
            df = prepare_frame(pd.read_parquet(local), loaded_at)
            ok, _, nrows, _ = writer(
                conn, df, loading,
                schema=RAW_SCHEMA,
                auto_create_table=True,
                overwrite=(i == 0),       # first part (re)creates the loading table
                use_logical_type=True,    # keep timestamps as timestamps
                quote_identifiers=False,
            )
            if not ok:
                raise RuntimeError(f"write_pandas failed for {part}")
            total += nrows
            local.unlink()

    cur = conn.cursor()
    try:
        cur.execute(f"CREATE TABLE IF NOT EXISTS {RAW_SCHEMA}.{table} LIKE {RAW_SCHEMA}.{loading}")
        cur.execute(f"ALTER TABLE {RAW_SCHEMA}.{table} SWAP WITH {RAW_SCHEMA}.{loading}")
        cur.execute(f"DROP TABLE IF EXISTS {RAW_SCHEMA}.{loading}")
    finally:
        cur.close()
    log.info("RAW.%s: %s rows from %s part file(s)", table, total, len(parts))
    return total


def load_all(conn, dbx: DatabricksFiles, tables: dict[str, str] | None = None) -> dict[str, int]:
    """Load every export folder into its RAW table. Called by the Airflow DAG."""
    tables = tables or EXPORT_TABLES
    return {
        table: load_table(conn, dbx, table, f"{EXPORT_ROOT}/{folder}")
        for table, folder in tables.items()
    }
