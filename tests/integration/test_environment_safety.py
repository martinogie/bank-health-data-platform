"""Integration: dev and CI really cannot write prod. The grant half of the safety net.

Logs in as the service user in .env (dev: SVC_BANK_DEV / DEV_ROLE) or in the CI job's
environment (ci: SVC_BANK_CI / CI_ROLE) and tries to write BANK_HEALTH. Every attempt must fail.
Each probe is harmless even if a grant were wrong: the INSERT copies zero rows, and anything a
probe did create is dropped immediately while the test fails loudly.

Skipped when BANK_HEALTH_ENV is prod (this test is about the non-prod identities) or when the
Snowflake values are missing. Run:
    pytest tests/integration/test_environment_safety.py -m integration
"""

from __future__ import annotations

import logging
import os

import pytest

from ingestion import environment
from tests.integration.conftest import require_env

pytestmark = pytest.mark.integration

PROBE = "IT_SAFETY_PROBE"
assert PROBE.startswith("IT_")


@pytest.fixture(scope="module")
def session():
    require_env(
        "BANK_HEALTH_ENV",
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_ROLE",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_PRIVATE_KEY_PATH",
    )
    env = environment.current_env()
    if env == "prod":
        pytest.skip("checks the dev and ci identities; BANK_HEALTH_ENV is prod")
    logging.getLogger("snowflake").setLevel(logging.ERROR)  # login URLs contain the account
    from ingestion.snowflake_load import connect_from_env

    conn = connect_from_env()
    cur = conn.cursor()
    yield env, cur
    cur.close()
    conn.close()


def _refused(cur, sql: str, cleanup: str | None = None) -> None:
    """`sql` must fail with a Snowflake error. If it succeeds, undo it and fail the test."""
    from snowflake.connector.errors import ProgrammingError

    try:
        cur.execute(sql)
    except ProgrammingError:
        return
    if cleanup:
        cur.execute(cleanup)
    pytest.fail(f"expected Snowflake to refuse: {sql}")


def test_session_is_the_non_prod_identity(session):
    env, cur = session
    cur.execute("select current_role(), current_database()")
    role, database = cur.fetchone()
    assert role == {"dev": "DEV_ROLE", "ci": "CI_ROLE"}[env]
    assert database == "BANK_HEALTH_DEV"


@pytest.mark.parametrize("schema", ["RAW", "STAGING", "MARTS"])
def test_cannot_create_tables_in_prod(session, schema):
    _, cur = session
    table = f"BANK_HEALTH.{schema}.{PROBE}"
    _refused(cur, f"create table {table} (x int)", cleanup=f"drop table if exists {table}")


def test_cannot_insert_into_prod(session):
    _, cur = session
    # Zero rows either way; Snowflake checks the INSERT privilege before running it.
    _refused(
        cur,
        "insert into BANK_HEALTH.RAW.FDIC_FINANCIALS select * from BANK_HEALTH.RAW.FDIC_FINANCIALS where 1 = 0",
    )


def test_cannot_create_schemas_in_prod(session):
    _, cur = session
    schema = f"BANK_HEALTH.{PROBE}"
    _refused(cur, f"create schema {schema}", cleanup=f"drop schema if exists {schema}")


def test_cannot_switch_to_the_prod_pipeline_role(session):
    _, cur = session
    _refused(cur, "use role BANK_PIPELINE_ROLE")


def test_cannot_write_the_manifest_stage(session):
    _, cur = session
    _refused(cur, "remove @BANK_HEALTH.RAW.DBT_ARTIFACTS/does_not_exist.json")


def test_dev_can_write_its_own_database(session):
    env, cur = session
    if env != "dev":
        pytest.skip("CI_ROLE writes only into its own CI_ schema (checked by the dbt slim CI job)")
    table = f"BANK_HEALTH_DEV.RAW.{PROBE}"
    cur.execute(f"create or replace table {table} (x int)")
    cur.execute(f"drop table {table}")


def test_ci_can_read_prod_for_defer(session):
    env, cur = session
    if env != "ci":
        pytest.skip("only CI_ROLE reads prod (dbt --defer)")
    cur.execute("select count(*) from BANK_HEALTH.MARTS.DIM_BANK")
    assert cur.fetchone()[0] > 0
    cur.execute("list @BANK_HEALTH.RAW.DBT_ARTIFACTS")  # READ on the manifest stage


def test_ci_user_has_no_private_key_on_disk_outside_the_runner():
    """The CI key exists only as a GitHub secret (written to the runner's temp folder per job)."""
    if os.environ.get("BANK_HEALTH_ENV") != "ci":
        pytest.skip("CI only")
    path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "")
    assert path.startswith(os.environ.get("RUNNER_TEMP", "\0")), "CI key must live in RUNNER_TEMP"
