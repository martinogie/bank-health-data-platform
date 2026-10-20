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
# The CSV export rejects very large requests (HTTP 400 "size"; seen at 123,553 rows,
# fine at 90,569). Windows above this are split in half until they fit.
MAX_ROWS_PER_REQUEST = 75_000


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


def _halves(start: date, end: date) -> tuple[tuple[date, date], tuple[date, date]]:
    mid = start + timedelta(days=(end - start).days // 2)
    return (start, mid), (mid + timedelta(days=1), end)


def download_window_parts(
    start: date, end: date, out_dir: Path, session: requests.Session | None = None
) -> list[Path]:
    """Download a window, splitting it into smaller date ranges if it is too large for
    one CSV export. Returns the files written (empty if there are no complaints)."""
    session = session or requests.Session()
    expected = count_complaints(session, start, end)
    if expected == 0:
        log.info("CFPB %s..%s: no complaints", start, end)
        return []
    if expected > MAX_ROWS_PER_REQUEST and start < end:
        log.info("CFPB %s..%s: %s rows is too large for one export, splitting", start, end, expected)
        return [p for a, b in _halves(start, end) for p in download_window_parts(a, b, out_dir, session)]
    try:
        path = download_window(start, end, out_dir, session, expected=expected)
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status == 400 and start < end:   # export refused as too large: split and retry
            log.info("CFPB %s..%s: export refused (400), splitting", start, end)
            return [p for a, b in _halves(start, end) for p in download_window_parts(a, b, out_dir, session)]
        raise
    return [path] if path else []


def existing_parts(out_dir: Path, start: date, end: date) -> list[Path] | None:
    """Files on disk that together cover exactly [start, end] (one whole-window file,
    or split parts that follow each other day by day). None if coverage is incomplete."""
    folder = Path(out_dir) / "complaints"
    if not folder.exists():
        return None
    parts = []
    for p in folder.glob("complaints_*_*.csv.gz"):
        try:
            a, b = (date.fromisoformat(x) for x in p.name[len("complaints_"):-len(".csv.gz")].split("_"))
        except ValueError:
            continue
        if start <= a and b <= end:
            parts.append((a, b, p))
    parts.sort()
    # prefer one file for the whole window if present
    whole = [p for a, b, p in parts if a == start and b == end]
    if whole:
        return whole
    expected_next, chain = start, []
    for a, b, p in parts:
        if a == expected_next:
            chain.append(p)
            expected_next = b + timedelta(days=1)
    return chain if chain and expected_next == end + timedelta(days=1) else None


def download_window(
    start: date, end: date, out_dir: Path, session: requests.Session | None = None,
    expected: int | None = None,
) -> Path | None:
    """Download one window to a gzipped CSV. Returns None if the window is empty."""
    session = session or requests.Session()
    if expected is None:
        expected = count_complaints(session, start, end)
    if expected == 0:
        log.info("CFPB %s..%s: no complaints", start, end)
        return None

    path = window_path(out_dir, start, end)
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


def window_path(out_dir: Path, start: date, end: date) -> Path:
    return Path(out_dir) / "complaints" / f"complaints_{start.isoformat()}_{end.isoformat()}.csv.gz"


def download_complaints(
    watermark: date | None, today: date, out_dir: Path, session: requests.Session | None = None
) -> dict[str, list[Path]]:
    """Download every window this run needs. Called by the Airflow DAG.

    Windows that ended more than LOOKBACK_DAYS ago are complete, so if their file is
    already on disk it is reused instead of downloaded again (cheap, safe retries).
    Returns {"fresh": files written this run, "cached": complete files reused}.
    """
    session = session or requests.Session()
    complete_before = today - timedelta(days=LOOKBACK_DAYS)
    fresh, cached = [], []
    for start, end in windows_to_download(watermark, today):
        existing = existing_parts(out_dir, start, end) if end < complete_before else None
        if existing:
            cached.extend(existing)
            continue
        fresh.extend(download_window_parts(start, end, out_dir, session))
    log.info("CFPB: %s windows downloaded, %s reused from disk", len(fresh), len(cached))
    return {"fresh": fresh, "cached": cached}


def read_csv_header(path: Path) -> list[str]:
    """Column names of a downloaded file (used by tests and sanity checks)."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh))
