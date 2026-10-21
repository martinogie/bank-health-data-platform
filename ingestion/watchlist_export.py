"""Build site/data/watchlist.json for the watchlist page from Snowflake MARTS.

Tables read:
  MARTS.MART_EARLY_WARNING   latest-quarter probability, risk band, reasons, complaints, model version
  MARTS.DIM_BANK             city/state, established date (bank age), CFPB match flag
  MARTS.MART_BANK_SCORECARD  last 8 quarters of ratios per bank, and peer medians by size tier

The file uses short keys and rounded numbers to stay small; the schema is documented in
site/data/README.md. Everything except `fetch_rows` is a pure function, so it is unit-tested
offline (ingestion/tests/test_watchlist_export.py).
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from pathlib import Path

SCHEMA_VERSION = 1
HISTORY_QUARTERS = 8
NEW_BANK_YEARS = 3.0          # younger banks: ratios distorted by start-up costs (review finding M-02)
MAX_BYTES = 3 * 1024 * 1024

# Watchlist metric key -> (scorecard column, label, unit, higher_is_better)
METRICS = {
    "ncl": ("noncurrent_loans_pct", "Non-current loans", "% of loans", False),
    "t1": ("tier1_leverage_pct", "Tier 1 leverage", "%", True),
    "roa": ("roa_pct", "Return on assets", "%", True),
    "eff": ("efficiency_ratio_pct", "Efficiency ratio", "%", False),
    "dep": ("deposit_growth_yoy_pct", "Deposit growth (year on year)", "%", None),
}

BAND_CODES = {"High": "H", "Elevated": "E", "Watch": "W", "Low": "L", "Already in distress": "D"}
TIER_CODES = {"1: <$100M": 1, "2: $100M-$1B": 2, "3: $1B-$10B": 3, "4: $10B-$100B": 4, "5: >$100B": 5}

# Model feature -> plain-language label and unit, for the reasons (features from _features.py)
FEATURE_LABELS = {
    "tier1_leverage_pct": ("Tier 1 leverage ratio", "pct"),
    "total_risk_based_capital_pct": ("Total risk-based capital ratio", "pct"),
    "equity_to_assets_pct": ("Equity to assets", "pct"),
    "noncurrent_loans_pct": ("Non-current loans", "pct_loans"),
    "net_chargeoff_rate_pct": ("Net charge-off rate", "pct"),
    "past_due_30_89_to_loans_pct": ("Loans 30–89 days past due", "pct_loans"),
    "past_due_90_to_loans_pct": ("Loans 90+ days past due", "pct_loans"),
    "nonaccrual_to_assets_pct": ("Non-accrual assets", "pct_assets"),
    "ore_to_assets_pct": ("Foreclosed property", "pct_assets"),
    "roa_pct": ("Return on assets", "pct"),
    "roe_pct": ("Return on equity", "pct"),
    "net_interest_margin_pct": ("Net interest margin", "pct"),
    "efficiency_ratio_pct": ("Efficiency ratio (costs per $1 of revenue)", "pct"),
    "net_income_qtr_to_assets_pct": ("Quarterly net income", "pct_assets"),
    "loans_to_deposits_pct": ("Loans to deposits", "pct"),
    "brokered_to_deposits_pct": ("Brokered deposits", "pct_deposits"),
    "loan_growth_qoq_pct": ("Loan growth over the quarter", "pct"),
    "loan_growth_yoy_pct": ("Loan growth over the year", "pct"),
    "deposit_growth_yoy_pct": ("Deposit growth over the year", "pct"),
    "asset_growth_yoy_pct": ("Asset growth over the year", "pct"),
    "cre_to_loans_pct": ("Commercial real estate loans", "pct_loans"),
    "cre_to_equity_pct": ("Commercial real estate loans", "pct_equity"),
    "construction_to_loans_pct": ("Construction loans", "pct_loans"),
    "ci_to_loans_pct": ("Business (C&I) loans", "pct_loans"),
    "consumer_to_loans_pct": ("Consumer loans", "pct_loans"),
    "residential_to_loans_pct": ("Residential mortgages", "pct_loans"),
    "ag_to_loans_pct": ("Farm loans", "pct_loans"),
    "log_total_assets": ("Size (total assets)", "log_dollars"),
    "noncurrent_change_1q_pp": ("Change in non-current loans over the quarter", "pp"),
    "noncurrent_change_4q_pp": ("Change in non-current loans over the year", "pp"),
    "tier1_change_4q_pp": ("Change in Tier 1 leverage over the year", "pp"),
    "roa_change_4q_pp": ("Change in return on assets over the year", "pp"),
}
_UNIT_SUFFIX = {"pct": "%", "pct_loans": "% of loans", "pct_assets": "% of assets",
                "pct_deposits": "% of deposits", "pct_equity": "% of equity"}
# reason text written by 05_score: "<feature> = <value> (median <value>)", values like 1,234.56 or n/a
_REASON_RE = re.compile(r"^\s*([a-z0-9_]+)\s*=\s*(n/a|-?[\d,]*\.?\d+)\s*\(median\s+(n/a|-?[\d,]*\.?\d+)\)\s*$")


# --------------------------------------------------------------- formatting
def round_or_none(value, digits: int):
    """Round a number, keeping None/NaN as None (JSON null)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    r = round(v, digits)
    return int(r) if digits == 0 else r


def _fmt_number(v: float) -> str:
    a = abs(v)
    if a >= 100:
        return f"{v:,.0f}"
    if a >= 10:
        return f"{v:,.1f}"
    return f"{v:,.2f}"


def _fmt_value(v: float | None, unit: str) -> str:
    if v is None:
        return "not reported"
    if unit == "pp":
        return f"{v:+.2f} pp"
    if unit == "log_dollars":
        dollars = math.exp(v)
        if dollars >= 1e9:
            return f"${dollars / 1e9:,.1f}bn"
        return f"${dollars / 1e6:,.0f}m"
    return _fmt_number(v) + _UNIT_SUFFIX.get(unit, "")


def _parse_number(text: str) -> float | None:
    return None if text == "n/a" else float(text.replace(",", ""))


def plain_reason(raw: str | None) -> str | None:
    """'noncurrent_loans_pct = 2.73 (median 0.46)' ->
    'Non-current loans: 2.73% of loans (typical bank: 0.46% of loans)'."""
    if not raw:
        return None
    m = _REASON_RE.match(raw)
    if not m:
        return raw.strip()                    # unknown format: show as-is rather than drop it
    feature, value, median = m.group(1), _parse_number(m.group(2)), _parse_number(m.group(3))
    label, unit = FEATURE_LABELS.get(feature, (feature.replace("_", " ").capitalize(), "num"))
    return f"{label}: {_fmt_value(value, unit)} (typical bank: {_fmt_value(median, unit)})"


# ------------------------------------------------------------------ dates
def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def bank_age_years(established, as_of) -> float | None:
    """Age in years at the data quarter, one decimal."""
    est, ref = _as_date(established), _as_date(as_of)
    if est is None or ref is None or est > ref:
        return None
    return round((ref - est).days / 365.25, 1)


def is_new_bank(age_years: float | None) -> bool:
    """Banks under NEW_BANK_YEARS old: start-up losses and costs distort their ratios."""
    return age_years is not None and age_years < NEW_BANK_YEARS


def quarter_ends(latest, n: int = HISTORY_QUARTERS) -> list[date]:
    """The n quarter-ends ending at `latest`, oldest first."""
    q = _as_date(latest)
    out = []
    for _ in range(n):
        out.append(q)
        y, m = (q.year, q.month - 3) if q.month > 3 else (q.year - 1, q.month + 9)
        q = _month_end(y, m)
    return list(reversed(out))


def _month_end(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def quarter_label(d) -> str:
    d = _as_date(d)
    return f"{d.year} Q{(d.month - 1) // 3 + 1}"


# --------------------------------------------------------------- history
def build_history(rows: list[dict], quarters: list[date], digits: int = 2) -> dict[str, list]:
    """One bank's rows -> {metric: [value per quarter, oldest first]}, None where missing.

    Rows may arrive in any order; they are placed by report_date, so the arrays always line
    up with `quarters`."""
    by_q = {_as_date(r["report_date"]): r for r in rows}
    return {
        key: [round_or_none(by_q[q][col], digits) if q in by_q else None for q in quarters]
        for key, (col, *_rest) in METRICS.items()
    }


# --------------------------------------------------------------- payload
def build_payload(bank_rows: list[dict], history_rows: list[dict], peer_rows: list[dict],
                  exported_on: date) -> dict:
    """Assemble the watchlist JSON from the three query results (lower-case column names)."""
    if not bank_rows:
        raise ValueError("no scored banks: MART_EARLY_WARNING is empty")
    latest = _as_date(bank_rows[0]["report_date"])
    quarters = quarter_ends(latest)

    hist_by_cert: dict[int, list[dict]] = {}
    for r in history_rows:
        hist_by_cert.setdefault(int(r["cert"]), []).append(r)

    peers: dict[str, dict[str, list]] = {}
    peer_by_tier: dict[int, list[dict]] = {}
    for r in peer_rows:
        tier = TIER_CODES.get(r["asset_size_tier"])
        if tier is not None:
            peer_by_tier.setdefault(tier, []).append(r)
    for tier, rows in sorted(peer_by_tier.items()):
        peers[str(tier)] = build_history(rows, quarters)

    banks = []
    for r in bank_rows:
        cert = int(r["cert"])
        age = bank_age_years(r.get("established_date"), latest)
        rec = {
            "c": cert,
            "n": r["bank_name"],
            "ci": r.get("city"),
            "st": r.get("state_code"),
            "a": round_or_none((r.get("total_assets_k") or 0) / 1000, 1),     # $ millions
            "age": age,
            "t": TIER_CODES.get(r.get("asset_size_tier")),
            "p": round_or_none(r.get("distress_probability"), 3),
            "b": BAND_CODES.get(r.get("risk_band"), "L"),
            "pp": round_or_none(None if r.get("peer_pct") is None else 100 * r["peer_pct"], 0),
            "r": [x for x in (plain_reason(r.get(f"reason_{i}")) for i in (1, 2, 3)) if x],
            "h": build_history(hist_by_cert.get(cert, []), quarters),
        }
        if is_new_bank(age):
            rec["nb"] = 1
        if r.get("has_cfpb_complaints"):
            rec["cx"] = round_or_none(r.get("complaints"), 0)
            rec["cr"] = round_or_none(r.get("complaints_per_1b_deposits"), 2)
        banks.append(rec)

    banks.sort(key=lambda b: (-(b["p"] or 0), b["c"]))
    first = bank_rows[0]
    return {
        "meta": {
            "schema": SCHEMA_VERSION,
            "quarter": latest.isoformat(),
            "quarterLabel": quarter_label(latest),
            "exported": exported_on.isoformat(),
            "model": first.get("model_name"),
            "modelVersion": round_or_none(first.get("model_version"), 0),
            "banks": len(banks),
            "history": [quarter_label(q) for q in quarters],
            "metrics": {k: {"label": v[1], "unit": v[2], "higherIsBetter": v[3]} for k, v in METRICS.items()},
            "bands": {v: k for k, v in BAND_CODES.items()},
            "newBankYears": NEW_BANK_YEARS,
        },
        "peers": peers,
        "banks": banks,
    }


def validate_payload(payload: dict) -> None:
    """Raise ValueError if the payload doesn't match the documented schema."""
    def need(cond, msg):
        if not cond:
            raise ValueError(f"watchlist.json schema: {msg}")

    need(set(payload) == {"meta", "peers", "banks"}, "top-level keys must be meta, peers, banks")
    meta = payload["meta"]
    for k in ("schema", "quarter", "quarterLabel", "exported", "model", "modelVersion", "banks", "history", "metrics"):
        need(k in meta, f"meta.{k} missing")
    n_hist = len(meta["history"])
    need(n_hist == HISTORY_QUARTERS, f"meta.history must have {HISTORY_QUARTERS} quarters")
    need(meta["banks"] == len(payload["banks"]), "meta.banks must equal number of banks")
    for tier, series in payload["peers"].items():
        need(tier in {"1", "2", "3", "4", "5"}, f"unknown peer tier {tier}")
        need(set(series) == set(METRICS) and all(len(v) == n_hist for v in series.values()),
             f"peers.{tier} must have every metric with {n_hist} values")
    seen = set()
    for b in payload["banks"]:
        need(isinstance(b.get("c"), int) and b["c"] not in seen, "bank CERT missing or duplicated")
        seen.add(b["c"])
        for k in ("n", "p", "b", "r", "h"):
            need(k in b, f"bank {b['c']}: {k} missing")
        need(b["b"] in BAND_CODES.values(), f"bank {b['c']}: unknown band {b['b']}")
        need(b["p"] is None or 0 <= b["p"] <= 1, f"bank {b['c']}: probability out of range")
        need(b.get("pp") is None or 0 <= b["pp"] <= 100, f"bank {b['c']}: percentile out of range")
        need(len(b["r"]) <= 3, f"bank {b['c']}: more than 3 reasons")
        need(set(b["h"]) == set(METRICS) and all(len(v) == n_hist for v in b["h"].values()),
             f"bank {b['c']}: history must have every metric with {n_hist} values")
        need(b.get("nb", 1) == 1, f"bank {b['c']}: nb must be 1 when present")


def write_payload(payload: dict, path: Path) -> int:
    """Validate, write compact JSON, and return its size in bytes (fails above 3 MB)."""
    validate_payload(payload)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        raise ValueError(f"watchlist.json would be {size:,} bytes, over the {MAX_BYTES:,} limit")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return size


# ---------------------------------------------------------------- Snowflake
BANKS_SQL = """
with ew as (select * from MARTS.MART_EARLY_WARNING),
     peer as (
        select cert,
               percent_rank() over (partition by asset_size_tier order by distress_probability) as peer_pct
        from ew
        where not already_in_distress
     )
select ew.cert, ew.bank_name, ew.city, ew.state_code, ew.asset_size_tier, ew.total_assets_k,
       ew.distress_probability, ew.risk_band, ew.already_in_distress,
       ew.reason_1, ew.reason_2, ew.reason_3,
       ew.complaints, ew.complaints_per_1b_deposits,
       ew.report_date, ew.model_name, ew.model_version,
       d.established_date, d.has_cfpb_complaints,
       peer.peer_pct
from ew
join MARTS.DIM_BANK d on d.cert = ew.cert
left join peer on peer.cert = ew.cert
"""

HISTORY_SQL = """
select s.cert, s.report_date, {cols}
from MARTS.MART_BANK_SCORECARD s
where s.report_date >= %(first_quarter)s
  and s.cert in (select cert from MARTS.MART_EARLY_WARNING)
""".format(cols=", ".join(f"s.{c}" for c, *_ in METRICS.values()))

PEERS_SQL = """
select asset_size_tier, report_date, {medians}
from MARTS.MART_BANK_SCORECARD
where report_date >= %(first_quarter)s
group by asset_size_tier, report_date
""".format(medians=", ".join(f"median({c}) as {c}" for c, *_ in METRICS.values()))


def _dict_rows(cur) -> list[dict]:
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_rows(conn) -> tuple[list[dict], list[dict], list[dict]]:
    """Run the three MARTS queries. Returns (banks, history, peers)."""
    cur = conn.cursor()
    try:
        cur.execute(BANKS_SQL)
        banks = _dict_rows(cur)
        if not banks:
            return banks, [], []
        first = quarter_ends(banks[0]["report_date"])[0]
        cur.execute(HISTORY_SQL, {"first_quarter": first})
        history = _dict_rows(cur)
        cur.execute(PEERS_SQL, {"first_quarter": first})
        peers = _dict_rows(cur)
    finally:
        cur.close()
    return banks, history, peers


def export(conn, out_path: Path, today: date | None = None) -> dict:
    """Fetch, build, validate and write. Returns a small summary for logs (no secrets)."""
    banks, history, peers = fetch_rows(conn)
    payload = build_payload(banks, history, peers, today or date.today())
    size = write_payload(payload, out_path)
    meta = payload["meta"]
    return {"path": str(out_path), "bytes": size, "banks": meta["banks"],
            "quarter": meta["quarterLabel"], "model_version": meta["modelVersion"]}
