"""Download consumer complaints from the CFPB Consumer Complaint Database API.

Public API, no key, licence CC0. We exclude the three national credit bureaus
(Equifax, TransUnion, Experian) on the server side: they make up ~80% of all
complaints and are not banks.

Files are saved as gzipped CSV exactly as the API delivers them, one file per
date window: <out_dir>/complaints/complaints_<start>_<end>.csv.gz
"""
from __future__ import annotations

import csv
import gzip
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API_URL = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"
USER_AGENT = "bank-health-data-platform (github.com/martinogie)"

# Exact company names as they appear in the CFPB data.
CREDIT_BUREAUS = [
    "EQUIFAX, INC.",
    "TRANSUNION INTERMEDIATE HOLDINGS, INC.",
    "Experian Information Solutions Inc.",
]
HISTORY_YEARS = 3
# Complaints are published after the company responds (up to ~15 days), so each
# run re-downloads this many days before the watermark. Silver dedupes on complaint_id.
LOOKBACK_DAYS = 30
# If the CSV has fewer rows than the API says exist, the export was truncated.
MIN_COMPLETENESS = 0.98


def history_start(today: date) -> date:
    """First day of the month HISTORY_YEARS ago."""
    return date(today.year - HISTORY_YEARS, today.month, 1)


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into calendar-month windows (first and last partial)."""
    windows = []
    cur = start
    while cur <= end:
        next_month = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        windows.append((cur, min(next_month - timedelta(days=1), end)))
        cur = next_month
    return windows


def windows_to_download(watermark: date | None, today: date) -> list[tuple[date, date]]:
    """Date windows this run should fetch.

    First run: HISTORY_YEARS of history. Later runs: from LOOKBACK_DAYS before the
    watermark (last date_received already loaded). Always stops at yesterday,
    because today's data is still being added.
    """
    end = today - timedelta(days=1)
    start = history_start(today) if watermark is None else watermark - timedelta(days=LOOKBACK_DAYS)
    if start > end:
        return []
    return month_windows(start, end)


def _params(start: date, end: date) -> list[tuple[str, str]]:
    # A list of pairs, because not_company is repeated once per bureau.
    params = [
        ("date_received_min", start.isoformat()),
        ("date_received_max", end.isoformat()),
        ("no_aggs", "true"),
    ]
    params += [("not_company", name) for name in CREDIT_BUREAUS]
    return params


def _get(session: requests.Session, params, stream: bool = False, retries: int = 4):
    for attempt in range(retries + 1):
        try:
            resp = session.get(
                API_URL, params=params, stream=stream, timeout=300,
                headers={"User-Agent": USER_AGENT},
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            retryable = status is None or status == 429 or status >= 500
            if attempt == retries or not retryable:
                raise
            wait = 2 ** attempt
            log.warning("CFPB request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def count_complaints(session: requests.Session, start: date, end: date) -> int:
    """How many complaints the API holds for the window (JSON, no rows returned)."""
    resp = _get(session, _params(start, end) + [("size", "0")])
    return int(resp.json()["hits"]["total"]["value"])


def count_csv_rows(path: Path) -> int:
    """Data rows in a gzipped CSV (uses the csv parser, so quoted newlines are safe)."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return max(sum(1 for _ in csv.reader(fh)) - 1, 0)


def download_window(
    start: date, end: date, out_dir: Path, session: requests.Session | None = None
) -> Path | None:
    """Download one window to a gzipped CSV. Returns None if the window is empty."""
    session = session or requests.Session()
    expected = count_complaints(session, start, end)
    if expected == 0:
        log.info("CFPB %s..%s: no complaints", start, end)
        return None

    path = Path(out_dir) / "complaints" / f"complaints_{start.isoformat()}_{end.isoformat()}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    resp = _get(session, _params(start, end) + [("format", "csv")], stream=True)
    with gzip.open(tmp, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            fh.write(chunk)

    rows = count_csv_rows(tmp)
    if rows < expected * MIN_COMPLETENESS:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"CFPB {start}..{end}: CSV has {rows} rows but API reports {expected}; "
            "export looks truncated"
        )
    tmp.replace(path)
    log.info("CFPB %s..%s: %s rows (API total %s) -> %s", start, end, rows, expected, path)
    return path


def download_complaints(
    watermark: date | None, today: date, out_dir: Path, session: requests.Session | None = None
) -> list[Path]:
    """Download every window this run needs. Called by the Airflow DAG."""
    session = session or requests.Session()
    paths = []
    for start, end in windows_to_download(watermark, today):
        path = download_window(start, end, out_dir, session)
        if path:
            paths.append(path)
    return paths


def read_csv_header(path: Path) -> list[str]:
    """Column names of a downloaded file (used by tests and sanity checks)."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh))
