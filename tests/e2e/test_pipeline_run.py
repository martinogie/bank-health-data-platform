"""End-to-end: after a bank_health_weekly DAG run, check the whole result.

    .venv\\Scripts\\python -m pytest tests/e2e -m e2e -v

1. Airflow: the latest bank_health_weekly run succeeded (Airflow REST API; credentials
   read from .env at runtime, never printed).
2. Snowflake: RAW and MARTS populated and consistent with each other.
3. Spot checks against the live sources: one bank's total assets (FDIC API) and one
   complete month's complaint count (CFPB API).
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import pytest
import requests

from ingestion import cfpb, fdic
from ingestion import snowflake_load as sl
from tests.integration.conftest import require_env

pytestmark = pytest.mark.e2e
AIRFLOW = "http://localhost:8080"
logging.getLogger("snowflake").setLevel(logging.WARNING)


@pytest.fixture(scope="module")
def sf():
    require_env("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH")
    conn = sl.connect_from_env()
    yield conn
    conn.close()


def one(conn, sql):
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return cur.fetchone()
    finally:
        cur.close()


# ------------------------------------------------------------------ Airflow
def test_latest_dag_run_succeeded():
    require_env("_AIRFLOW_WWW_USER_USERNAME", "_AIRFLOW_WWW_USER_PASSWORD")
    token = requests.post(f"{AIRFLOW}/auth/token", timeout=30, json={
        "username": os.environ["_AIRFLOW_WWW_USER_USERNAME"],
        "password": os.environ["_AIRFLOW_WWW_USER_PASSWORD"],
    }).json()["access_token"]
    runs = requests.get(f"{AIRFLOW}/api/v2/dags/bank_health_weekly/dagRuns", timeout=30,
                        headers={"Authorization": f"Bearer {token}"},
                        params={"order_by": "-start_date", "limit": 1}).json()["dag_runs"]
    assert runs, "no DAG runs yet"
    assert runs[0]["state"] == "success", f"latest run {runs[0]['dag_run_id']} is {runs[0]['state']}"


# ---------------------------------------------------------------- Snowflake
TABLE_MINIMUMS = {
    "RAW.FDIC_FINANCIALS": 700_000, "RAW.FDIC_INSTITUTIONS": 25_000, "RAW.FDIC_FAILURES": 4_000,
    "RAW.CFPB_COMPLAINTS": 1_000_000, "RAW.ML_RISK_SCORES": 3_500,
    "MARTS.DIM_BANK": 25_000, "MARTS.FCT_BANK_QUARTER": 700_000, "MARTS.FCT_COMPLAINTS": 1_000_000,
    "MARTS.MART_BANK_SCORECARD": 700_000, "MARTS.MART_EARLY_WARNING": 3_500,
}


@pytest.mark.parametrize("table,minimum", TABLE_MINIMUMS.items())
def test_tables_populated(sf, table, minimum):
    assert one(sf, f"select count(*) from BANK_HEALTH.{table}")[0] >= minimum


def test_raw_and_marts_consistent(sf):
    assert one(sf, "select count(*) from BANK_HEALTH.RAW.CFPB_COMPLAINTS")[0] == \
        one(sf, "select count(*) from BANK_HEALTH.MARTS.FCT_COMPLAINTS")[0]
    assert one(sf, "select count(*) from BANK_HEALTH.RAW.FDIC_INSTITUTIONS")[0] == \
        one(sf, "select count(*) from BANK_HEALTH.MARTS.DIM_BANK")[0]
    assert one(sf, "select count(*) from BANK_HEALTH.RAW.ML_RISK_SCORES")[0] == \
        one(sf, "select count(*) from BANK_HEALTH.MARTS.MART_EARLY_WARNING")[0]


def test_marts_have_fdic_latest_quarter(sf):
    latest = fdic.latest_financials_quarter()
    assert one(sf, "select max(report_date) from BANK_HEALTH.MARTS.FCT_BANK_QUARTER")[0] == latest
    assert one(sf, "select max(report_date) from BANK_HEALTH.MARTS.MART_EARLY_WARNING")[0] == latest


def test_complaint_history_covers_three_years(sf):
    lo, hi = one(sf, "select min(date_received), max(date_received) from BANK_HEALTH.RAW.CFPB_COMPLAINTS")
    assert (hi - lo).days >= 33 * 30
    assert (date.today() - hi).days <= 10


def test_loaded_recently(sf):
    hours = one(sf, "select datediff(hour, min(_loaded_at), current_timestamp()) from BANK_HEALTH.RAW.CFPB_COMPLAINTS")[0]
    assert hours <= 8 * 24


# ------------------------------------------------------- live source checks
def test_spot_check_bank_total_assets_vs_fdic(sf):
    """JPMorgan Chase Bank (CERT 628), latest quarter: Snowflake equals the FDIC API."""
    latest = fdic.latest_financials_quarter()
    payload = fdic._get(requests.Session(), "financials", {
        "filters": f"CERT:628 AND REPDTE:{fdic.to_repdte(latest)}", "fields": "ASSET", "format": "json"})
    api_assets = payload["data"][0]["data"]["ASSET"]
    ours = one(sf, f"select total_assets_k from BANK_HEALTH.MARTS.FCT_BANK_QUARTER "
                   f"where cert = 628 and report_date = '{latest}'")[0]
    assert ours == api_assets


def test_spot_check_month_complaint_count_vs_cfpb(sf):
    """A complete month 3 months back: our count equals the CFPB API (bureaus excluded)."""
    first = (date.today().replace(day=1) - timedelta(days=80)).replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    api = cfpb.count_complaints(requests.Session(), first, last)
    ours = one(sf, f"select count(*) from BANK_HEALTH.MARTS.FCT_COMPLAINTS "
                   f"where date_received between '{first}' and '{last}'")[0]
    # The CFPB keeps publishing a few late complaints for old months; allow 0.5%.
    assert abs(ours - api) <= 0.005 * api, f"{first:%Y-%m}: ours {ours} vs CFPB API {api}"
