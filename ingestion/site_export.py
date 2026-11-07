"""Build the Bank Health website's data files (finance.martinsogiekpolor.com/bankhealth) from one
successful pipeline run. The data contract is site/data/SCHEMA.md; the JSON Schemas and the model
figures are in ingestion/site_contract/ (shared with the site's own validator).

Files (paths relative to the site's data/ folder): overview.json, banks_index.json, model.json,
pipeline.json and banks/<cert>.json for every bank.

Everything except `fetch` is a pure function, unit-tested offline. The same functions build the
Stage A preview data (site/scripts/build_fixtures.py, "fixture": true) and the real weekly data
(the bank_health_publish_site DAG, "fixture": false). There is no hand-written text about any
bank: each bank's one-line summary is generated from its call report.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CONTRACT_DIR = Path(__file__).resolve().parent / "site_contract"

# SHAP groups for the "push" figures and the driver chip (decision 1b, 6 Nov 2026). Features from
# databricks/notebooks/_features.py; anything else (capital, liquidity, growth, size) is "other".
CREDIT = {
    "noncurrent_loans_pct", "net_chargeoff_rate_pct", "past_due_30_89_to_loans_pct",
    "past_due_90_to_loans_pct", "nonaccrual_to_assets_pct", "ore_to_assets_pct",
    "noncurrent_change_1q_pp", "noncurrent_change_4q_pp",
    "cre_to_loans_pct", "cre_to_equity_pct", "construction_to_loans_pct", "ci_to_loans_pct",
    "consumer_to_loans_pct", "residential_to_loans_pct", "ag_to_loans_pct",
}  # fmt: skip
EARNINGS = {
    "roa_pct", "roe_pct", "net_interest_margin_pct", "efficiency_ratio_pct",
    "net_income_qtr_to_assets_pct", "roa_change_4q_pp",
}  # fmt: skip

# Band meanings from the observed distress rates in the model card ("Bands": 2003-2019 / 2020-2024):
# H 50.0% / 43.8%, E 25.0% / 20.0%, W 10.0% / 6.5%, L 2.8% / 1.2%.
BAND_MEANING = {
    "H": {"min": 0.358, "max": None, "meaning": "About 4 to 5 in 10 enter distress within a year"},
    "E": {"min": 0.163, "max": 0.358, "meaning": "About 1 in 5 to 1 in 4"},
    "W": {"min": 0.036, "max": 0.163, "meaning": "About 1 in 15 to 1 in 10"},
    "L": {"min": None, "max": 0.036, "meaning": "Fewer than 3 in 100"},
    "D": {"min": None, "max": None, "meaning": "Meets a distress test now"},
    "N": {"min": None, "max": None, "meaning": "Under three years old: not scored"},
    "F": {"min": None, "max": None, "meaning": "US office of a foreign bank: not scored"},
}
# Model card, "Band counts" (bank-quarters per band; 2003-2019 out of sample, 2020-2024 test).
BANDS_BY_YEAR = [
    (2003, 2256, 2814, 7482, 18455), (2004, 1864, 2673, 7817, 18593), (2005, 1451, 2238, 7508, 19523),
    (2006, 1354, 2168, 6980, 19164), (2007, 1720, 2645, 7817, 15696), (2008, 2901, 3528, 8166, 9706),
    (2009, 4015, 3556, 6897, 5480), (2010, 3897, 3552, 6777, 4525), (2011, 3610, 3633, 7261, 4630),
    (2012, 2995, 3517, 7854, 5442), (2013, 2668, 3387, 8449, 6184), (2014, 1921, 2917, 8589, 7865),
    (2015, 1377, 2370, 7916, 9634), (2016, 1117, 2014, 7140, 10520), (2017, 1020, 1719, 6291, 10984),
    (2018, 860, 1427, 5535, 11491), (2019, 832, 1406, 5241, 11074), (2020, 772, 1512, 5398, 10281),
    (2021, 467, 934, 4186, 12080), (2022, 515, 938, 4405, 11576), (2023, 610, 1160, 5106, 10134),
    (2024, 813, 1355, 4758, 9296),
]  # fmt: skip


# ------------------------------------------------------------------ formatting
def _half_up(x: float, places: int) -> str:
    return str(Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def money_m(m: float) -> str:
    """One rounding rule for dollar amounts in $ millions (same as money() in site/src/lib/site.ts):
    $X.Xbn from $1bn, whole millions from $10m, $X.Xm from $1m, whole thousands below."""
    a = abs(m)
    if a >= 1000:
        return f"${_half_up(a / 1000, 1)}bn"
    if a >= 10:
        return f"${_half_up(a, 0)}m"
    if a >= 1:
        return f"${_half_up(a, 1)}m"
    return f"${_half_up(a * 1000, 0)}k"


SIZE_REASON = re.compile(
    r"Size \(total assets\): \$[\d.,]+(?:k|m|bn)( \(median of similar-size banks: \$[\d.,]+(?:k|m|bn)\))?"
)


def size_reason(text: str, assets_m: float | None, w: dict | None) -> str:
    """Re-format the size reason with money_m (the scoring export rounds differently: $6m vs $6.5m)."""
    if assets_m is None or not SIZE_REASON.search(text):
        return text
    med = next(
        (
            i["m"]
            for i in (w or {}).get("i", [])
            if i.get("f") == "log_total_assets" and i.get("m") is not None
        ),
        None,
    )
    tail = f" (median of similar-size banks: {money_m(math.exp(med) / 1e6)})" if med is not None else ""
    return SIZE_REASON.sub(f"Size (total assets): {money_m(assets_m)}{tail}", text)


def summary(quarter: str, ni_k: float | None, assets_k: float | None) -> dict | None:
    """The bank's one-line summary, generated from its call report (never hand-written)."""
    if ni_k is None or not assets_k:
        return None
    kind = "net loss" if ni_k < 0 else "net income"
    pct = _half_up(abs(100 * ni_k / assets_k), 2)
    sign = "−" if ni_k < 0 else ""
    return {
        "text": f"{quarter}: {kind} of {money_m(abs(ni_k) / 1000)} in the quarter ({sign}{pct}% of assets), "
        f"on total assets of {money_m(assets_k / 1000)}.",
        "source": f"FDIC call report, {quarter}",
    }


def pushes(waterfall: dict | None) -> tuple[dict | None, str | None]:
    """(pushes in percentage points by group, driver) from the SHAP waterfall's items."""
    if not waterfall:
        return None, None
    credit = sum(i["pp"] for i in waterfall["i"] if i["f"] in CREDIT)
    earnings = sum(i["pp"] for i in waterfall["i"] if i["f"] in EARNINGS)
    out = {"credit": round(credit, 1), "earnings": round(earnings, 1)}
    best = max(out, key=lambda k: out[k])
    return out, (best if out[best] > 0 else None)


def quarter_label(d: date | str) -> str:
    d = date.fromisoformat(str(d)[:10])
    return f"{d.year} Q{(d.month - 1) // 3 + 1}"


def _iso(t: Any) -> str | None:
    """ISO text in UTC for datetimes (Snowflake rows) or strings (JSON inputs)."""
    if t is None:
        return None
    if isinstance(t, datetime):
        return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    return str(t)


def _dt(t: Any) -> datetime:
    return datetime.fromisoformat(str(_iso(t)).replace("Z", "+00:00"))


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


# ------------------------------------------------------------------ the files
def header(meta: dict, run: dict, fixture: bool) -> dict:
    ended = _iso(run["ended_at"]) or ""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "fixture": fixture,
        "generatedAt": ended,
        "quarter": meta["quarter"],
        "dataAsOf": {"quarterLabel": meta["quarterLabel"], "updated": ended[:10]},
    }


def run_entry(h: dict, override: dict | None = None) -> dict:
    """One row of the Pipeline page's "Recent runs". A run resumed after a fix (failed, fixed, tasks
    cleared) is one entry timed from its first start, with each earlier attempt kept: from the
    health view's RESUMED / EARLIER_ATTEMPTS, or `override` (the preview data's Airflow history)."""
    minutes = h.get("duration_minutes")
    entry = {
        "date": str(_iso(h["started_at"]))[:10],
        "state": h["state"],
        "durationSeconds": round(float(minutes) * 60) if minutes is not None else None,
        "databricksMinutes": h.get("databricks_minutes"),
        "credits": h.get("credits"),
        "banksScored": h.get("scored_banks"),
        "resumed": False,
        "earlierAttempts": [],
    }
    first = override.get("first_started_at") if override else None
    attempts = (override or {}).get("attempts") or (
        _json(h.get("earlier_attempts")) if h.get("resumed") else None
    )
    if first:
        entry["date"] = str(first)[:10]
        entry["durationSeconds"] = round((_dt(h["ended_at"]) - _dt(first)).total_seconds())
    if attempts:
        entry["resumed"] = True
        entry["earlierAttempts"] = [
            {
                "state": a["state"],
                "failedTasks": a.get("failed_tasks"),
                "durationSeconds": round((_dt(a["ended_at"]) - _dt(a["started_at"])).total_seconds())
                if a.get("ended_at") and a.get("started_at")
                else None,
            }
            for a in attempts
        ]
    return entry


def build_site(
    watchlist: dict,
    waterfalls: dict[int, dict],
    ni_rows: list[dict],
    run: dict,
    health: list[dict],
    model: dict,
    *,
    fixture: bool,
    preview: set[int] | None = None,
    resumed_overrides: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """{relative path: JSON document} for every site data file.

    watchlist: the watchlist payload (schema 5, ingestion/watchlist_export.build_payload);
    waterfalls: {cert: waterfall file} (build_waterfalls, {"c", "q", "w"});
    ni_rows: {cert, report_date, net_income_qtr_k, total_assets_k} for the 8 quarters;
    run: the run's PIPELINE_RUNS row (lower-case keys); health: OPS.HEALTH_RUNS_LAST_12 rows;
    model: ingestion/site_contract/model_v5.json; preview: only these banks get a file (Stage A)."""
    meta, banks = watchlist["meta"], watchlist["banks"]
    rows = _json(run["rows_detail"]) or {}
    head = header(meta, run, fixture)
    labels = meta["history"]
    out: dict[str, dict] = {}

    scored = sorted((b for b in banks if b["b"] in "HEWL" and b.get("p") is not None), key=lambda b: -b["p"])
    rank = {b["c"]: i + 1 for i, b in enumerate(scored)}

    ni: dict[int, dict[str, float]] = {}
    latest_ni: dict[int, tuple[float, float]] = {}
    for r in ni_rows:
        lab = quarter_label(r["report_date"])
        if r.get("total_assets_k") and r.get("net_income_qtr_k") is not None:
            ni_k, assets_k = float(r["net_income_qtr_k"]), float(r["total_assets_k"])
            ni.setdefault(int(r["cert"]), {})[lab] = round(100 * ni_k / assets_k, 2)
            if lab == meta["quarterLabel"]:
                latest_ni[int(r["cert"])] = (ni_k, assets_k)

    wfs = {b["c"]: (waterfalls.get(b["c"]) or {}).get("w") for b in banks if b.get("wf")}
    reasons = {
        b["c"]: [size_reason(t, b.get("a"), wfs.get(b["c"])) for t in (b.get("r") or [])] for b in banks
    }

    def first_reason(cert: int) -> str | None:
        return reasons[cert][0] if reasons[cert] else None

    index = {**head, "banks": [
        {"c": b["c"], "n": b["n"], "ci": b.get("ci"), "st": b.get("st"), "a": b.get("a"), "b": b["b"],
         "p": b.get("p"), "r": first_reason(b["c"])}
        for b in banks
    ]}  # fmt: skip
    if preview is not None:
        index["preview"] = sorted(preview)
    out["banks_index.json"] = index

    drivers: dict[int, str | None] = {}
    for b in banks:
        w = wfs.get(b["c"])
        push, driver = pushes(w)
        drivers[b["c"]] = driver
        if preview is not None and b["c"] not in preview:
            continue
        flags = (["newBank"] if b["b"] == "N" or b.get("nb") else []) + (
            ["foreignOffice"] if b["b"] == "F" else []
        )
        history = dict(b["h"])
        history["ni"] = [ni.get(b["c"], {}).get(q) for q in labels]
        out[f"banks/{b['c']}.json"] = {**head,
            "profile": {"name": b["n"], "city": b.get("ci"), "state": b.get("st"), "cert": b["c"],
                        "assetsM": b.get("a"), "ageYears": b.get("age"), "tier": b.get("t")},
            "probability": b.get("p"), "band": b["b"], "rank": rank.get(b["c"]), "scoredBanks": len(scored),
            "peerPercentile": b.get("pp"),
            "reasons": [{"text": t} for t in reasons[b["c"]]],
            "pushes": push, "driver": driver, "waterfall": w, "distressLines": b.get("d"),
            "history": history, "quarters": labels,
            "complaints": ({"quarter": meta["quarterLabel"], "count": b.get("cx"), "per1bDeposits": b.get("cr")}
                           if b.get("cx") is not None else None),
            "flags": flags, "note": summary(meta["quarterLabel"], *latest_ni.get(b["c"], (None, None))),
        }  # fmt: skip

    counts = {k: sum(1 for b in banks if b["b"] == k) for k in "HEWLDNF"}
    out["overview.json"] = {**head,
        "modelVersion": meta["modelVersion"], "banksScored": len(scored), "bandCounts": counts,
        "bandMeaning": BAND_MEANING,
        "keyFigures": {"bankQuarters": rows["gold"]["bank_quarter"], "failures": rows["silver"]["fdic_failures"]},
        "bandsByYear": [{"year": y, "H": h, "E": e, "W": w, "L": low} for y, h, e, w, low in BANDS_BY_YEAR],
        "top20": [{"rank": rank[b["c"]], "cert": b["c"], "name": b["n"], "state": b.get("st"),
                   "probability": b["p"], "band": b["b"], "reason": first_reason(b["c"]),
                   "driver": drivers.get(b["c"])} for b in scored[:20]],
    }  # fmt: skip

    out["model.json"] = {**head, **{k: v for k, v in model.items() if not k.startswith("_")}}

    overrides = resumed_overrides or {}
    this = next((h for h in health if h["run_id"] == run["run_id"]), {})
    datasets = {
        "fdic_financials": ("Financials", "fdic_financials", "bank_quarter"),
        "fdic_institutions": ("Institutions", "fdic_institutions", None),
        "fdic_failures": ("Failures", "fdic_failures", None),
        "cfpb_complaints": ("Complaints", "cfpb_complaints", None),
    }
    out["pipeline.json"] = {**head,
        "datasets": [{"key": k, "label": label, "bronze": rows["bronze"][src], "silver": rows["silver"][src],
                      "gold": rows["gold"][gold] if gold else None} for k, (label, src, gold) in datasets.items()],
        "marts": rows["marts"],
        "complaintMatchPct": rows["match"]["complaints_matched_to_bank_pct"],
        "runs": [run_entry(h, overrides.get(h["run_id"])) for h in health],
        "latest": {"dbtModelsOk": run["dbt_models_ok"], "dbtTestsPass": run["dbt_tests_pass"],
                   "dbtTestsWarn": this.get("dbt_tests_warn", 0) or 0,
                   "dbtTestsFail": run["dbt_tests_fail"], "databricksMinutes": run.get("databricks_minutes"),
                   "durationSeconds": run["duration_seconds"]},
    }  # fmt: skip
    return out


def load_model() -> dict:
    return json.loads((CONTRACT_DIR / "model_v5.json").read_text(encoding="utf-8"))


def encode(docs: dict[str, dict]) -> dict[str, bytes]:
    """The files as compact UTF-8 JSON bytes, ready to scan and upload."""
    return {
        p: json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8") for p, d in docs.items()
    }


# ------------------------------------------------------------------ Snowflake
NI_SQL = """
select cert, report_date, net_income_qtr_k, total_assets_k
from MARTS.MART_BANK_SCORECARD
where report_date >= %(first_quarter)s
"""
RUN_SQL = """
select RUN_ID, STARTED_AT, ENDED_AT, DURATION_SECONDS, STATE, ROWS_DETAIL, SCORED_BANKS,
       DBT_MODELS_OK, DBT_TESTS_PASS, DBT_TESTS_FAIL, DATABRICKS_MINUTES
from OPS.PIPELINE_RUNS
where STATE = 'success' and ROWS_DETAIL:gold is not null and ROWS_DETAIL:backfill is null
order by ENDED_AT desc
limit 1
"""
HEALTH_SQL = "select * from OPS.HEALTH_RUNS_LAST_12 order by STARTED_AT desc"


def _rows(cur) -> list[dict]:
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch(conn) -> dict[str, Any]:
    """Everything the files need, read from Snowflake (the connection's database: BANK_HEALTH in prod,
    BANK_HEALTH_DEV in dev): the watchlist inputs, net income, the latest successful weekly run (not
    a backfill) and the last 12 runs."""
    from ingestion import watchlist_export as wx

    banks, history, peers = wx.fetch_rows(conn)
    cur = conn.cursor()
    try:
        cur.execute(RUN_SQL)
        runs = _rows(cur)
        if not runs:
            raise ValueError("no successful weekly run in OPS.PIPELINE_RUNS to publish")
        cur.execute(HEALTH_SQL)
        health = _rows(cur)
        first = wx.quarter_ends(banks[0]["report_date"])[0] if banks else None
        ni_rows: list[dict] = []
        if first:
            cur.execute(NI_SQL, {"first_quarter": first})
            ni_rows = _rows(cur)
    finally:
        cur.close()
    run = runs[0]
    return {
        "watchlist": wx.build_payload(banks, history, peers, date.today()),
        "waterfalls": wx.build_waterfalls(banks),
        "ni_rows": ni_rows,
        "run": run,
        "health": health,
    }
