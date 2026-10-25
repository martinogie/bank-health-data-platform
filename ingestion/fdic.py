"""Download bank data from the FDIC BankFind Suite API (public, no key needed).

Three datasets:
  financials    quarterly call-report figures per bank (amounts in $ thousands)
  institutions  bank name, location, charter class, holding company
  failures      list of failed banks (used for the distress label)

Each dataset is saved as gzipped JSON Lines: one bank row per line, exactly as
the API returned it. Cleaning happens later in Databricks (bronze keeps raw).
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.fdic.gov/banks"
PAGE_SIZE = 10_000  # API maximum per request
# Starting in 2000 gives a year of look-back so 2001 rows can have YoY features.
HISTORY_START = date(2000, 3, 31)
# Banks can amend recent call reports, so each run re-downloads this many quarters.
REFRESH_QUARTERS = 2

FINANCIAL_FIELDS = [
    "CERT", "REPDTE",
    # size and balance sheet
    "ASSET", "DEP", "DEPDOM", "BRO", "LNLSNET", "LNLSGR", "EQ", "ORE",
    # earnings
    "NETINC", "ROA", "ROE", "NIMY", "EEFFR",
    # asset quality
    "NCLNLSR", "NTLNLSR", "ELNATR", "P3ASSET", "P9ASSET", "NAASSET",
    # capital
    "RBC1AAJ", "RBCRWAJ",
    # loan mix (CRE concentration = construction + multifamily + nonfarm nonresidential)
    "LNRE", "LNRECONS", "LNREMULT", "LNRENRES", "LNRERES", "LNCI", "LNCON", "LNAG",
    # reporting framework flags (see docs/investigations/model_credibility.md):
    # CBLRIND = 1: Community Bank Leverage Ratio bank, reports no risk-based capital (from 2020)
    # IBA = 1: International Banking Act entity, i.e. a US office of a foreign bank
    "CBLRIND", "IBA",
]

INSTITUTION_FIELDS = [
    "CERT", "NAME", "CITY", "STALP", "STNAME", "ZIP", "ACTIVE", "INACTIVE",
    "BKCLASS", "CHARTER", "REGAGNT", "SPECGRP", "SPECGRPN", "NAMEHCR", "RSSDHCR",
    "FED_RSSD", "ESTYMD", "INSDATE", "ENDEFYMD", "CHANGEC1", "ASSET", "DEP",
    "OFFICES", "WEBADDR", "REPDTE",
]


# --------------------------------------------------------------------------- #
# Quarter helpers
# --------------------------------------------------------------------------- #
_QUARTER_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))


def quarter_end_on_or_before(d: date) -> date:
    """Most recent calendar quarter-end that is not after `d`."""
    for month, day in reversed(_QUARTER_ENDS):
        q = date(d.year, month, day)
        if q <= d:
            return q
    return date(d.year - 1, 12, 31)


def quarter_ends(start: date, end: date) -> list[date]:
    """All quarter-end dates from `start` to `end`, inclusive."""
    out = []
    for year in range(start.year, end.year + 1):
        for month, day in _QUARTER_ENDS:
            q = date(year, month, day)
            if start <= q <= end:
                out.append(q)
    return out


def to_repdte(d: date) -> str:
    """date(2026, 6, 30) -> '20260630' (the FDIC REPDTE format)."""
    return d.strftime("%Y%m%d")


def quarters_to_download(last_loaded: date | None, latest_available: date) -> list[date]:
    """Which quarters this run should fetch.

    First run (nothing loaded yet): the full history from HISTORY_START.
    Later runs: anything newer than `last_loaded`, plus the last REFRESH_QUARTERS
    quarters again to pick up amended call reports.
    """
    available = quarter_ends(HISTORY_START, latest_available)
    if last_loaded is None or not available:
        return available
    refresh_from = available[-REFRESH_QUARTERS:][0]
    return [q for q in available if q > last_loaded or q >= refresh_from]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _get(session: requests.Session, endpoint: str, params: dict, retries: int = 4) -> dict:
    """GET one page, retrying on network errors and 429/5xx with backoff."""
    url = f"{BASE_URL}/{endpoint}"
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=120)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            retryable = status is None or status == 429 or status >= 500
            if attempt == retries or not retryable:
                raise
            wait = 2 ** attempt
            log.warning("FDIC %s failed (%s), retrying in %ss", endpoint, exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def fetch_all(
    session: requests.Session,
    endpoint: str,
    fields: Iterable[str] | None = None,
    filters: str | None = None,
    sort_by: str = "CERT",
) -> Iterator[dict]:
    """Yield every record from an FDIC endpoint, following limit/offset pages."""
    offset = 0
    total = None
    while total is None or offset < total:
        params = {
            "limit": PAGE_SIZE,
            "offset": offset,
            "sort_by": sort_by,
            "sort_order": "ASC",
            "format": "json",
        }
        if fields:
            params["fields"] = ",".join(fields)
        if filters:
            params["filters"] = filters
        payload = _get(session, endpoint, params)
        total = payload["meta"]["total"]
        rows = payload.get("data", [])
        if not rows:
            break
        for row in rows:
            yield row["data"]  # each record is wrapped as {"data": {...}, "score": n}
        offset += len(rows)


def latest_financials_quarter(session: requests.Session | None = None) -> date:
    """The newest REPDTE the FDIC has published."""
    payload = _get(session or requests.Session(), "financials", {
        "fields": "REPDTE", "sort_by": "REPDTE", "sort_order": "DESC", "limit": 1, "format": "json",
    })
    repdte = payload["data"][0]["data"]["REPDTE"]
    return date(int(repdte[:4]), int(repdte[4:6]), int(repdte[6:]))


# --------------------------------------------------------------------------- #
# File writing
# --------------------------------------------------------------------------- #
def write_jsonl_gz(records: Iterable[dict], path: Path) -> int:
    """Write records as gzipped JSON Lines; returns the row count.

    Writes to a temp file first so a crash never leaves a half-written file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            count += 1
    tmp.replace(path)
    return count


# --------------------------------------------------------------------------- #
# Public download functions (called by the Airflow DAG)
# --------------------------------------------------------------------------- #
def financials_path(out_dir: Path, q: date) -> Path:
    return Path(out_dir) / "financials" / f"financials_{to_repdte(q)}.jsonl.gz"


def has_all_fields(path: Path, fields: Iterable[str] = FINANCIAL_FIELDS, sample: int = 50) -> bool:
    """True if a cached file was downloaded with every field we now request.

    The API returns requested fields even when null, so a field missing from the first rows
    means the file is from before that field was added (e.g. CBLRIND, IBA) and is stale.
    """
    wanted = set(fields)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if wanted <= set(json.loads(line)):
                    return True
                if i >= sample:
                    break
    except (OSError, ValueError):
        return False
    return False


def split_cached_quarters(
    quarters: list[date], out_dir: Path, latest_available: date
) -> tuple[list[date], list[Path]]:
    """Separate quarters that must be downloaded from ones already complete on disk.

    The newest REFRESH_QUARTERS are always re-downloaded (amendments); older quarters
    are reused if their file exists and has every requested field (files from before a
    field was added are downloaded again). Makes a retried or repeated run cheap and safe.
    """
    refresh_from = quarter_ends(HISTORY_START, latest_available)[-REFRESH_QUARTERS:][0]
    to_fetch, cached = [], []
    for q in quarters:
        path = financials_path(out_dir, q)
        if q < refresh_from and path.exists() and has_all_fields(path):
            cached.append(path)
        else:
            to_fetch.append(q)
    return to_fetch, cached


def download_financials(
    quarters: Iterable[date], out_dir: Path, session: requests.Session | None = None
) -> list[Path]:
    """One file per quarter: <out_dir>/financials/financials_YYYYMMDD.jsonl.gz"""
    session = session or requests.Session()
    paths = []
    for q in quarters:
        repdte = to_repdte(q)
        path = financials_path(out_dir, q)
        n = write_jsonl_gz(
            fetch_all(session, "financials", FINANCIAL_FIELDS, filters=f"REPDTE:{repdte}"), path
        )
        if n == 0:
            log.warning("No financials returned for %s; skipping file", repdte)
            path.unlink(missing_ok=True)
            continue
        log.info("financials %s: %s banks -> %s", repdte, n, path)
        paths.append(path)
    return paths


def download_institutions(
    out_dir: Path, load_date: date, session: requests.Session | None = None
) -> Path:
    """All institutions, active and inactive (inactive ones matter for history)."""
    session = session or requests.Session()
    path = Path(out_dir) / "institutions" / f"institutions_{load_date.isoformat()}.jsonl.gz"
    n = write_jsonl_gz(fetch_all(session, "institutions", INSTITUTION_FIELDS), path)
    log.info("institutions: %s rows -> %s", n, path)
    return path


def download_failures(
    out_dir: Path, load_date: date, session: requests.Session | None = None
) -> Path:
    """Full failed-bank list, all fields (about 4k rows, one page)."""
    session = session or requests.Session()
    path = Path(out_dir) / "failures" / f"failures_{load_date.isoformat()}.jsonl.gz"
    n = write_jsonl_gz(fetch_all(session, "failures", fields=None, sort_by="FAILDATE"), path)
    log.info("failures: %s rows -> %s", n, path)
    return path
