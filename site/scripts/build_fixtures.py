"""Stage A: build the website's preview data (site/public/data/) from REAL data published today.

Inputs (site/.fixture-inputs/, git-ignored, pulled once from prod): watchlist.json + waterfalls/
(schema 5, this week's export), snowflake_inputs.json (read-only extracts: quarterly net income for
the preview banks, the latest successful run, the last runs from OPS.HEALTH_RUNS_LAST_12),
preview_certs.json (the banks that get a full file). Model figures are copied from docs/model_card.md.
No hand-written text about any bank: each bank's summary is generated from its FDIC figures.

Every file is marked "fixture": true; the site shows a "preview data" label. Stage B replaces this
script with scripts/export_site_data.py (same files, from the marts, every week).

    python site/scripts/build_fixtures.py
"""

from __future__ import annotations

import json
import math
import re
import shutil
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
IN = SITE / ".fixture-inputs"
OUT = SITE / "public" / "data"
SCHEMA_VERSION = 1

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



def _half_up(x: float, places: int) -> str:
    return str(Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def money_m(m: float) -> str:
    """One rounding rule for dollar amounts in $ millions (same as money() in src/lib/site.ts):
    $X.Xbn from $1bn, whole millions from $10m, $X.Xm from $1m, whole thousands below."""
    a = abs(m)
    if a >= 1000:
        return f"${_half_up(a / 1000, 1)}bn"
    if a >= 10:
        return f"${_half_up(a, 0)}m"
    if a >= 1:
        return f"${_half_up(a, 1)}m"
    return f"${_half_up(a * 1000, 0)}k"


SIZE_REASON = re.compile(r"Size \(total assets\): \$[\d.,]+(?:k|m|bn)( \(median of similar-size banks: \$[\d.,]+(?:k|m|bn)\))?")


def size_reason(text: str, assets_m: float | None, w: dict | None) -> str:
    """Re-format the size reason with money_m (the backend rounds differently: $6m vs $6.5m)."""
    if assets_m is None or not SIZE_REASON.search(text):
        return text
    med = next((i["m"] for i in (w or {}).get("i", []) if i.get("f") == "log_total_assets" and i.get("m") is not None), None)
    tail = f" (median of similar-size banks: {money_m(math.exp(med) / 1e6)})" if med is not None else ""
    return SIZE_REASON.sub(f"Size (total assets): {money_m(assets_m)}{tail}", text)


def summary(quarter: str, ni_k: float | None, assets_k: float | None) -> dict | None:
    """The bank's one-line summary, generated from its call report (never hand-written)."""
    if ni_k is None or not assets_k:
        return None
    kind = "net loss" if ni_k < 0 else "net income"
    pct = _half_up(abs(100 * ni_k / assets_k), 2)
    sign = "−" if ni_k < 0 else ""
    return {"text": f"{quarter}: {kind} of {money_m(abs(ni_k) / 1000)} in the quarter ({sign}{pct}% of assets), "
                    f"on total assets of {money_m(assets_k / 1000)}.",
            "source": f"FDIC call report, {quarter}"}


# Band meaning (model card, "Bands"): probability cut-offs and the share that became distressed.
BAND_MEANING = {
# Meanings from the observed distress rates in the model card ("Bands": 2003-2019 / 2020-2024):
# H 50.0% / 43.8%, E 25.0% / 20.0%, W 10.0% / 6.5%, L 2.8% / 1.2%.
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


def header(meta: dict, run: dict) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "fixture": True,
        "generatedAt": run["ended_at"],
        "quarter": meta["quarter"],
        "dataAsOf": {"quarterLabel": meta["quarterLabel"], "updated": run["ended_at"][:10]},
    }


def pushes(waterfall: dict | None) -> tuple[dict | None, str | None]:
    """(pushes in percentage points by group, driver) from the waterfall's items."""
    if not waterfall:
        return None, None
    credit = sum(i["pp"] for i in waterfall["i"] if i["f"] in CREDIT)
    earnings = sum(i["pp"] for i in waterfall["i"] if i["f"] in EARNINGS)
    out = {"credit": round(credit, 1), "earnings": round(earnings, 1)}
    best = max(out, key=out.get)
    return out, (best if out[best] > 0 else None)


def main() -> None:
    wl = json.loads((IN / "watchlist.json").read_text(encoding="utf-8"))
    sf = json.loads((IN / "snowflake_inputs.json").read_text(encoding="utf-8"))
    preview = set(json.loads((IN / "preview_certs.json").read_text(encoding="utf-8")))
    meta, run = wl["meta"], sf["run"][0]
    rows = run["rows_detail"] if isinstance(run["rows_detail"], dict) else json.loads(run["rows_detail"])
    banks = wl["banks"]
    head = header(meta, run)

    def waterfall_of(cert: int) -> dict | None:
        f = IN / "waterfalls" / f"{cert}.json"
        return json.loads(f.read_text(encoding="utf-8"))["w"] if f.exists() else None

    scored = sorted((b for b in banks if b["b"] in "HEWL" and b.get("p") is not None), key=lambda b: -b["p"])
    rank = {b["c"]: i + 1 for i, b in enumerate(scored)}

    # net income % of assets per bank and quarter (8 quarters, aligned with meta.history)
    labels = meta["history"]
    ni: dict[int, dict[str, float]] = {}
    latest_ni: dict[int, tuple[float, float]] = {}
    for r in sf["ni"]:
        y, m = int(r["report_date"][:4]), int(r["report_date"][5:7])
        lab = f"{y} Q{(m - 1) // 3 + 1}"
        if r["total_assets_k"] and r["net_income_qtr_k"] is not None:
            ni.setdefault(int(r["cert"]), {})[lab] = round(100 * r["net_income_qtr_k"] / r["total_assets_k"], 2)
            if lab == meta["quarterLabel"]:
                latest_ni[int(r["cert"])] = (r["net_income_qtr_k"], r["total_assets_k"])

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "banks").mkdir(parents=True)

    def write(rel: str, doc: dict) -> None:
        (OUT / rel).write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    wfs = {b["c"]: waterfall_of(b["c"]) for b in banks if b.get("wf")}
    reasons = {b["c"]: [size_reason(t, b.get("a"), wfs.get(b["c"])) for t in (b.get("r") or [])] for b in banks}

    # banks_index.json: every bank (search)
    write("banks_index.json", {**head, "preview": sorted(preview), "banks": [
        {"c": b["c"], "n": b["n"], "ci": b.get("ci"), "st": b.get("st"), "a": b.get("a"), "b": b["b"],
         "p": b.get("p"), "r": (reasons[b["c"]] or [None])[0]}
        for b in banks
    ]})  # fmt: skip

    # banks/<cert>.json: the preview banks
    drivers = {}
    for b in banks:
        w = wfs.get(b["c"])
        push, driver = pushes(w)
        drivers[b["c"]] = driver
        if b["c"] not in preview:
            continue
        flags = (["newBank"] if b["b"] == "N" or b.get("nb") else []) + (["foreignOffice"] if b["b"] == "F" else [])
        history = {k: v for k, v in b["h"].items()}
        history["ni"] = [ni.get(b["c"], {}).get(q) for q in labels]
        write(f"banks/{b['c']}.json", {**head,
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
        })  # fmt: skip

    # overview.json
    counts = {k: sum(1 for b in banks if b["b"] == k) for k in "HEWLDNF"}
    write("overview.json", {**head,
        "modelVersion": meta["modelVersion"], "banksScored": len(scored), "bandCounts": counts,
        "bandMeaning": BAND_MEANING,
        "keyFigures": {"bankQuarters": rows["gold"]["bank_quarter"], "failures": rows["silver"]["fdic_failures"]},
        "bandsByYear": [{"year": y, "H": h, "E": e, "W": w, "L": l} for y, h, e, w, l in BANDS_BY_YEAR],
        "top20": [{"rank": rank[b["c"]], "cert": b["c"], "name": b["n"], "state": b.get("st"),
                   "probability": b["p"], "band": b["b"], "reason": (reasons[b["c"]] or [None])[0],
                   "driver": drivers.get(b["c"])} for b in scored[:20]],
    })  # fmt: skip

    # model.json (figures copied from docs/model_card.md, v5)
    write("model.json", {**head, **json.loads((SITE / "content" / "model_v5.json").read_text(encoding="utf-8"))})

    # pipeline.json. Resumed runs (failed, fixed, tasks cleared) are shown as one run with the
    # failed attempt kept: from the health view's RESUMED / EARLIER_ATTEMPTS in Stage B; for the
    # two runs logged before that fix, from Airflow's own task history (airflow_attempts.json).
    att_file = IN / "airflow_attempts.json"
    resumed = {k: v for k, v in json.loads(att_file.read_text(encoding="utf-8")).items()
               if not k.startswith("_")} if att_file.exists() else {}  # fmt: skip

    def iso(t: str) -> datetime:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))

    def run_entry(h: dict, r: dict | None) -> dict:
        entry = {"date": h["started_at"][:10], "state": h["state"],
                 "durationSeconds": round(h["duration_minutes"] * 60) if h.get("duration_minutes") is not None else None,
                 "databricksMinutes": h.get("databricks_minutes"), "credits": h.get("credits"),
                 "banksScored": h.get("scored_banks"), "resumed": False, "earlierAttempts": []}  # fmt: skip
        if r:
            first, end = iso(r["first_started_at"]), iso(h["ended_at"])
            entry.update(date=r["first_started_at"][:10], resumed=True,
                         durationSeconds=round((end - first).total_seconds()),
                         earlierAttempts=[{"state": a["state"], "failedTasks": a["failed_tasks"],
                                           "durationSeconds": round((iso(a["ended_at"]) - iso(a["started_at"])).total_seconds())}
                                          for a in r["attempts"]])  # fmt: skip
        return entry

    datasets = {
        "fdic_financials": ("Financials", rows["bronze"]["fdic_financials"], rows["silver"]["fdic_financials"], rows["gold"]["bank_quarter"]),
        "fdic_institutions": ("Institutions", rows["bronze"]["fdic_institutions"], rows["silver"]["fdic_institutions"], None),
        "fdic_failures": ("Failures", rows["bronze"]["fdic_failures"], rows["silver"]["fdic_failures"], None),
        "cfpb_complaints": ("Complaints", rows["bronze"]["cfpb_complaints"], rows["silver"]["cfpb_complaints"], None),
    }  # fmt: skip
    write("pipeline.json", {**head,
        "datasets": [{"key": k, "label": v[0], "bronze": v[1], "silver": v[2], "gold": v[3]} for k, v in datasets.items()],
        "marts": rows["marts"],
        "complaintMatchPct": rows["match"]["complaints_matched_to_bank_pct"],
        "runs": [run_entry(h, resumed.get(h["run_id"])) for h in sf["health"]],
        "latest": {"dbtModelsOk": run["dbt_models_ok"], "dbtTestsPass": run["dbt_tests_pass"],
                   "dbtTestsWarn": next(h["dbt_tests_warn"] for h in sf["health"] if h["run_id"] == run["run_id"]),
                   "dbtTestsFail": run["dbt_tests_fail"], "databricksMinutes": run["databricks_minutes"],
                   "durationSeconds": run["duration_seconds"]},
    })  # fmt: skip
    n = len(list((OUT / "banks").iterdir()))
    print(f"fixtures: {len(banks)} banks in the index, {n} bank files, top 20, model, pipeline -> {OUT}")


if __name__ == "__main__":
    main()
