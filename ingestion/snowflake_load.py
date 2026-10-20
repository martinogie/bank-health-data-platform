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

from ingestion.databricks_files import CATALOG, DatabricksFiles

log = logging.getLogger(__name__)

EXPORT_ROOT = f"/Volumes/{CATALOG}/gold/exports"
RAW_SCHEMA = "RAW"

# Snowflake RAW table  ->  export folder written by the Databricks notebooks
EXPORT_TABLES = {
    "FDIC_FINANCIALS": "fdic_financials",
    "FDIC_INSTITUTIONS": "fdic_institutions",
    "FDIC_FAILURES": "fdic_failures",
    "CFPB_COMPLAINTS": "cfpb_complaints",
    "ML_RISK_SCORES": "ml_risk_scores",
}
# Exports that may not exist yet (risk scores arrive with the model in step 4).
OPTIONAL_TABLES = {"ML_RISK_SCORES"}

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_key_path(path: str) -> str:
    """Use the configured key path; if it's the container path and we're on the PC,
    fall back to the same file name in the project's keys/ folder."""
    if Path(path).exists():
        return path
    local = PROJECT_ROOT / "keys" / PurePosixPath(path).name
    return str(local) if local.exists() else path


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
        private_key_file=resolve_key_path(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]),
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


def _is_missing(exc: Exception) -> bool:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(exc, FileNotFoundError) or status == 404


def load_all(conn, dbx: DatabricksFiles, tables: dict[str, str] | None = None) -> dict[str, int]:
    """Load every export folder into its RAW table. Called by the Airflow DAG.

    A missing export fails the load, except for OPTIONAL_TABLES, which are skipped.
    """
    tables = tables or EXPORT_TABLES
    loaded = {}
    for table, folder in tables.items():
        try:
            loaded[table] = load_table(conn, dbx, table, f"{EXPORT_ROOT}/{folder}")
        except Exception as exc:
            if table in OPTIONAL_TABLES and _is_missing(exc):
                log.info("RAW.%s skipped: no export at %s/%s yet", table, EXPORT_ROOT, folder)
                continue
            raise
    return loaded


if __name__ == "__main__":
    # Manual run from the project folder:  python -m ingestion.snowflake_load
    import sys

    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from _env import load_env

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("snowflake").setLevel(logging.WARNING)  # connector is very chatty at INFO
    load_env()
    conn = connect_from_env()
    try:
        counts = load_all(conn, DatabricksFiles.from_env())
    finally:
        conn.close()
    for table, n in counts.items():
        print(f"RAW.{table}: {n} rows")
