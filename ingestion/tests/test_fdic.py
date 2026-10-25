import gzip
import json
from datetime import date

import pytest
import requests

from ingestion import fdic
from ingestion.tests.conftest import FakeResponse, FakeSession


def page(records, total):
    return {"meta": {"total": total}, "data": [{"data": r, "score": 0} for r in records]}


# ------------------------------------------------------------------ quarters
def test_quarter_end_on_or_before():
    assert fdic.quarter_end_on_or_before(date(2026, 10, 19)) == date(2026, 9, 30)
    assert fdic.quarter_end_on_or_before(date(2026, 6, 30)) == date(2026, 6, 30)
    assert fdic.quarter_end_on_or_before(date(2026, 2, 1)) == date(2025, 12, 31)


def test_quarter_ends_inclusive():
    qs = fdic.quarter_ends(date(2025, 6, 30), date(2026, 3, 31))
    assert qs == [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31)]


def test_to_repdte():
    assert fdic.to_repdte(date(2026, 6, 30)) == "20260630"


def test_first_run_downloads_full_history():
    qs = fdic.quarters_to_download(None, date(2026, 6, 30))
    assert qs[0] == fdic.HISTORY_START
    assert qs[-1] == date(2026, 6, 30)
    assert len(qs) == 4 * 26 + 2  # 2000Q1..2025Q4 plus 2026Q1, Q2


def test_incremental_run_gets_new_quarter_plus_refresh():
    qs = fdic.quarters_to_download(date(2026, 3, 31), date(2026, 6, 30))
    assert qs == [date(2026, 3, 31), date(2026, 6, 30)]


def test_incremental_run_with_nothing_new_still_refreshes():
    qs = fdic.quarters_to_download(date(2026, 6, 30), date(2026, 6, 30))
    assert qs == [date(2026, 3, 31), date(2026, 6, 30)]


def test_incremental_run_after_gap_fills_missing_quarters():
    qs = fdic.quarters_to_download(date(2025, 9, 30), date(2026, 6, 30))
    assert qs == [date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]


def write_cached(path, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    fdic.write_jsonl_gz([{f: 1 for f in fields}], path)


def test_cached_file_without_new_fields_is_downloaded_again(tmp_path):
    latest = date(2026, 6, 30)
    old = [f for f in fdic.FINANCIAL_FIELDS if f not in ("CBLRIND", "IBA")]
    write_cached(fdic.financials_path(tmp_path, date(2025, 9, 30)), old)               # before the change
    write_cached(fdic.financials_path(tmp_path, date(2025, 12, 31)), fdic.FINANCIAL_FIELDS)
    to_fetch, cached = fdic.split_cached_quarters([date(2025, 9, 30), date(2025, 12, 31)], tmp_path, latest)
    assert to_fetch == [date(2025, 9, 30)]
    assert cached == [fdic.financials_path(tmp_path, date(2025, 12, 31))]


def test_financial_fields_include_reporting_flags():
    assert {"CBLRIND", "IBA"} <= set(fdic.FINANCIAL_FIELDS)


def test_split_cached_quarters_reuses_old_files_but_refreshes_latest(tmp_path):
    latest = date(2026, 6, 30)
    for q in (date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)):
        write_cached(fdic.financials_path(tmp_path, q), fdic.FINANCIAL_FIELDS)
    quarters = [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]
    to_fetch, cached = fdic.split_cached_quarters(quarters, tmp_path, latest)
    # 2025-09-30 has no file -> fetch; 2025-12-31 on disk and old -> reuse;
    # the last 2 quarters are always re-downloaded even though files exist
    assert to_fetch == [date(2025, 9, 30), date(2026, 3, 31), date(2026, 6, 30)]
    assert cached == [fdic.financials_path(tmp_path, date(2025, 12, 31))]


# ---------------------------------------------------------------- pagination
def test_fetch_all_follows_offsets(monkeypatch):
    monkeypatch.setattr(fdic, "PAGE_SIZE", 2)
    records = [{"CERT": i} for i in range(5)]

    def handler(method, url, kw):
        off = kw["params"]["offset"]
        return FakeResponse(payload=page(records[off:off + 2], total=5))

    session = FakeSession(handler)
    out = list(fdic.fetch_all(session, "financials", ["CERT"], filters="REPDTE:20260630"))
    assert out == records
    assert [c[2]["params"]["offset"] for c in session.calls] == [0, 2, 4]
    assert session.calls[0][2]["params"]["filters"] == "REPDTE:20260630"
    assert session.calls[0][2]["params"]["fields"] == "CERT"


def test_get_retries_server_errors(no_sleep):
    responses = iter([FakeResponse(503), FakeResponse(payload=page([{"CERT": 1}], 1))])
    session = FakeSession(lambda *a: next(responses))
    assert list(fdic.fetch_all(session, "failures")) == [{"CERT": 1}]
    assert len(session.calls) == 2


def test_get_does_not_retry_client_errors(no_sleep):
    session = FakeSession(lambda *a: FakeResponse(400))
    with pytest.raises(requests.HTTPError):
        list(fdic.fetch_all(session, "failures"))
    assert len(session.calls) == 1


def test_latest_financials_quarter():
    session = FakeSession(lambda *a: FakeResponse(payload=page([{"REPDTE": "20260630"}], 1)))
    assert fdic.latest_financials_quarter(session) == date(2026, 6, 30)


# ------------------------------------------------------------------- writing
def test_download_financials_writes_one_file_per_quarter(tmp_path):
    def handler(method, url, kw):
        repdte = kw["params"]["filters"].split(":")[1]
        rows = [] if repdte == "20260331" else [{"CERT": 1, "REPDTE": repdte, "ASSET": 100}]
        return FakeResponse(payload=page(rows, len(rows)))

    paths = fdic.download_financials(
        [date(2026, 3, 31), date(2026, 6, 30)], tmp_path, FakeSession(handler)
    )
    assert [p.name for p in paths] == ["financials_20260630.jsonl.gz"]  # empty quarter skipped
    assert not (tmp_path / "financials" / "financials_20260331.jsonl.gz").exists()
    with gzip.open(paths[0], "rt") as fh:
        assert [json.loads(line) for line in fh] == [
            {"CERT": 1, "REPDTE": "20260630", "ASSET": 100}
        ]


def test_download_institutions_and_failures_named_by_load_date(tmp_path):
    session = FakeSession(lambda *a: FakeResponse(payload=page([{"CERT": 7}], 1)))
    inst = fdic.download_institutions(tmp_path, date(2026, 10, 19), session)
    fail = fdic.download_failures(tmp_path, date(2026, 10, 19), session)
    assert inst.name == "institutions_2026-10-19.jsonl.gz"
    assert fail.name == "failures_2026-10-19.jsonl.gz"
    assert "fields" not in session.calls[1][2]["params"]  # failures: all fields


def test_write_leaves_no_temp_file(tmp_path):
    path = tmp_path / "x.jsonl.gz"
    assert fdic.write_jsonl_gz([{"a": 1}, {"a": 2}], path) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["x.jsonl.gz"]
