"""Integration: Snowflake key-pair login, write_pandas, and the real load/swap path.

Only ever touches its own table RAW.IT_SMOKE_TEST (and IT_SMOKE_TEST__LOADING),
and drops both afterwards, pass or fail. Never touches the real RAW tables.
Needs SNOWFLAKE_* values in .env and the private key in keys/.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ingestion import snowflake_load as sl
from tests.integration.conftest import require_env

pytestmark = pytest.mark.integration

TEST_TABLE = "IT_SMOKE_TEST"
assert TEST_TABLE.startswith("IT_")  # the drop below must only ever hit test tables


@pytest.fixture(scope="module")
def conn():
    require_env(
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_ROLE",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_PRIVATE_KEY_PATH",
    )
    connection = sl.connect_from_env()
    yield connection
    cur = connection.cursor()
    try:
        for name in (TEST_TABLE, f"{TEST_TABLE}__LOADING"):
            cur.execute(f"DROP TABLE IF EXISTS {sl.RAW_SCHEMA}.{name}")
    finally:
        cur.close()
        connection.close()


def query(conn, sql):
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return cur.fetchall()
    finally:
        cur.close()


# The service identity each environment logs in as (snowflake/setup.sql, setup_dev.sql).
IDENTITIES = {
    "prod": ("SVC_BANK_PIPELINE", "BANK_PIPELINE_ROLE", "BANK_HEALTH"),
    "dev": ("SVC_BANK_DEV", "DEV_ROLE", "BANK_HEALTH_DEV"),
}


def test_key_pair_login_uses_the_environments_identity(conn):
    from ingestion.environment import current_env

    env = current_env()
    if env not in IDENTITIES:
        pytest.skip(f"no RAW load identity in {env}")
    user, role, db, schema = query(
        conn, "select current_user(), current_role(), current_database(), current_schema()"
    )[0]
    assert (user, role, db) == IDENTITIES[env]
    assert schema == "RAW"


def test_write_pandas_round_trip(conn):
    from snowflake.connector.pandas_tools import write_pandas

    df = pd.DataFrame({"CERT": [628, 3511, 7213], "BANK": ["JPM", "WFC", "C"], "RATIO": [7.49, 8.1, 9.0]})
    ok, _, nrows, _ = write_pandas(
        conn,
        df,
        TEST_TABLE,
        schema=sl.RAW_SCHEMA,
        auto_create_table=True,
        overwrite=True,
        quote_identifiers=False,
    )
    assert ok and nrows == 3
    rows = query(conn, f"select CERT, BANK, RATIO from {sl.RAW_SCHEMA}.{TEST_TABLE} order by CERT")
    assert rows == [(628, "JPM", 7.49), (3511, "WFC", 8.1), (7213, "C", 9.0)]


class InMemoryExports:
    """Serves parquet bytes as if they were Databricks Volume files."""

    def __init__(self, parts: dict[str, bytes]):
        self.parts = parts

    def list_directory(self, _):
        return [{"path": p, "is_directory": False} for p in self.parts]

    def download_file(self, path, local):
        local.write_bytes(self.parts[path])
        return local


def test_load_table_swaps_atomically(conn, tmp_path):
    parts = {}
    for i, frame in enumerate([pd.DataFrame({"cert": [1, 2]}), pd.DataFrame({"cert": [3]})]):
        p = tmp_path / f"p{i}.parquet"
        frame.to_parquet(p)
        parts[f"/Volumes/test/part-{i}.parquet"] = p.read_bytes()

    n = sl.load_table(conn, InMemoryExports(parts), TEST_TABLE, "/Volumes/test")
    assert n == 3
    rows = query(conn, f"select CERT, _LOADED_AT is not null from {sl.RAW_SCHEMA}.{TEST_TABLE} order by CERT")
    assert rows == [(1, True), (2, True), (3, True)]  # old write_pandas rows fully replaced
    leftover = query(conn, f"show tables like '{TEST_TABLE}__LOADING' in schema {sl.RAW_SCHEMA}")
    assert leftover == []
