from datetime import date

import pytest

from ingestion import cfpb
from ingestion.tests.conftest import FakeResponse, FakeSession

HEADER = "Date received,Product,Company,Complaint ID\n"


def csv_bytes(n_rows, with_multiline=False):
    rows = [f'2026-09-0{1 + i % 9},Mortgage,BANK {i},{1000 + i}\n' for i in range(n_rows)]
    if with_multiline and rows:
        rows[0] = '2026-09-01,"Checking\nor savings",BANK 0,1000\n'
    return (HEADER + "".join(rows)).encode()


def handler_for(expected, body):
    def handler(method, url, kw):
        params = dict(kw["params"])
        if params.get("format") == "csv":
            return FakeResponse(content=body)
        return FakeResponse(payload={"hits": {"total": {"value": expected}}})
    return handler


# ------------------------------------------------------------------- windows
def test_month_windows_handles_partial_months_and_year_end():
    w = cfpb.month_windows(date(2025, 11, 15), date(2026, 1, 10))
    assert w == [
        (date(2025, 11, 15), date(2025, 11, 30)),
        (date(2025, 12, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 10)),
    ]


def test_first_run_covers_three_years_to_yesterday():
    w = cfpb.windows_to_download(None, date(2026, 10, 19))
    assert w[0][0] == date(2023, 10, 1)
    assert w[-1][1] == date(2026, 10, 18)
    assert len(w) == 37


def test_incremental_run_looks_back_30_days():
    w = cfpb.windows_to_download(date(2026, 10, 11), date(2026, 10, 19))
    assert w == [(date(2026, 9, 11), date(2026, 9, 30)), (date(2026, 10, 1), date(2026, 10, 18))]


def test_params_exclude_all_three_bureaus():
    params = cfpb._params(date(2026, 9, 1), date(2026, 9, 30))
    excluded = [v for k, v in params if k == "not_company"]
    assert sorted(excluded) == sorted(cfpb.CREDIT_BUREAUS)
    assert ("date_received_min", "2026-09-01") in params
    assert ("date_received_max", "2026-09-30") in params


# ------------------------------------------------------------------ download
def test_download_window_writes_gzipped_csv(tmp_path):
    session = FakeSession(handler_for(3, csv_bytes(3, with_multiline=True)))
    path = cfpb.download_window(date(2026, 9, 1), date(2026, 9, 30), tmp_path, session)
    assert path.name == "complaints_2026-09-01_2026-09-30.csv.gz"
    assert cfpb.count_csv_rows(path) == 3  # quoted newline counted as one row
    assert cfpb.read_csv_header(path) == ["Date received", "Product", "Company", "Complaint ID"]
    assert session.calls[0][2]["headers"]["User-Agent"] == cfpb.USER_AGENT


def test_truncated_export_raises_and_leaves_no_file(tmp_path):
    session = FakeSession(handler_for(100, csv_bytes(50)))
    with pytest.raises(RuntimeError, match="truncated"):
        cfpb.download_window(date(2026, 9, 1), date(2026, 9, 30), tmp_path, session)
    assert list((tmp_path / "complaints").iterdir()) == []


def test_empty_window_returns_none_without_downloading(tmp_path):
    session = FakeSession(handler_for(0, b""))
    assert cfpb.download_window(date(2026, 9, 1), date(2026, 9, 2), tmp_path, session) is None
    assert len(session.calls) == 1  # only the count request


def test_download_complaints_skips_empty_windows(tmp_path):
    def handler(method, url, kw):
        params = dict(kw["params"])
        empty = params["date_received_min"].startswith("2026-09")
        if params.get("format") == "csv":
            return FakeResponse(content=csv_bytes(2))
        return FakeResponse(payload={"hits": {"total": {"value": 0 if empty else 2}}})

    paths = cfpb.download_complaints(date(2026, 10, 11), date(2026, 10, 19), tmp_path, FakeSession(handler))
    assert [p.name for p in paths] == ["complaints_2026-10-01_2026-10-18.csv.gz"]
