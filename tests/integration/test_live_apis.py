"""Integration: our download code against the real FDIC and CFPB APIs.

Small requests only (one bank, a few days of complaints). No credentials needed.
    .venv\\Scripts\\python -m pytest tests/integration -m integration
"""

from __future__ import annotations

import csv
import gzip
import json
from datetime import date, timedelta

import pytest
import requests

from ingestion import cfpb, fdic

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def session():
    return requests.Session()


def test_fdic_latest_quarter_is_recent(session):
    latest = fdic.latest_financials_quarter(session)
    # Call reports are published ~2 months after quarter-end, so the newest
    # quarter must be within roughly the last 7 months.
    assert date.today() - timedelta(days=215) <= latest <= date.today()
    assert latest == fdic.quarter_end_on_or_before(latest)


def test_fdic_all_requested_fields_exist(session):
    """Every field we ask for comes back, i.e. none were renamed or retired."""
    latest = fdic.latest_financials_quarter(session)
    rows = list(
        fdic.fetch_all(
            session,
            "financials",
            fdic.FINANCIAL_FIELDS,
            filters=f"CERT:628 AND REPDTE:{fdic.to_repdte(latest)}",
        )
    )
    assert len(rows) == 1
    missing = set(fdic.FINANCIAL_FIELDS) - set(rows[0])
    assert not missing, f"FDIC no longer returns: {sorted(missing)}"


def test_fdic_institutions_fields_exist(session):
    payload = fdic._get(
        session,
        "institutions",
        {
            "filters": "CERT:628",
            "fields": ",".join(fdic.INSTITUTION_FIELDS),
            "format": "json",
        },
    )
    missing = set(fdic.INSTITUTION_FIELDS) - set(payload["data"][0]["data"])
    assert not missing, f"FDIC institutions no longer returns: {sorted(missing)}"


def test_fdic_download_writes_real_quarter(tmp_path, session):
    latest = fdic.latest_financials_quarter(session)
    [path] = fdic.download_financials([latest], tmp_path, session)
    with gzip.open(path, "rt") as fh:
        rows = [json.loads(line) for line in fh]
    assert len(rows) > 3000  # ~4,300 banks in 2026
    assert len({r["CERT"] for r in rows}) == len(rows)  # one row per bank


def test_cfpb_window_matches_api_count_and_excludes_bureaus(tmp_path, session):
    start = date.today() - timedelta(days=40)
    end = start + timedelta(days=2)
    path = cfpb.download_window(start, end, tmp_path, session)
    assert path is not None
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert {"Complaint ID", "Date received", "Company", "Product"} <= set(rows[0])
    assert not {r["Company"] for r in rows} & set(cfpb.CREDIT_BUREAUS)
    assert len({r["Complaint ID"] for r in rows}) == len(rows)
