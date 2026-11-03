r"""E-01: precision and recall of the complaint-to-bank matching, from the label file.

    .venv\Scripts\python scripts/e01_metrics.py

Reads docs/investigations/e01_match_labels.csv and e01_population.json (nothing else), prints the
results and writes docs/investigations/e01_metrics.json. Offline and deterministic (fixed seed).

Which label counts: Martins' label if he confirmed or corrected the row, else the proposed label.
Rows marked unsure (proposed_label, confirmed_by_martins or martins_label) are left
out. A company in both the random unmatched sample and the top-50 list is counted once: in the
top-50 census, and left out of the random sample of "the rest".

- Precision (of the matches made): from the random matched sample. By company: Wilson 95%. By
  complaint volume: bootstrap over companies (volume-weighted shares are not binomial).
- Recall (of the complaints that belong to a bank, the share matched): matched-correct volume =
  precision by volume x all matched complaints; missed volume = true misses in the top 50 (a
  census) + the rest of the unmatched estimated from the random sample. With no true miss in
  the random sample, the rest adds 0; the upper end assumes the rest misses at the Wilson upper
  bound of that zero rate.
"""

from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "docs" / "investigations"
Z = 1.959964
SEED = 20261103
BOOT = 10_000
PROVENANCE = (
    "Labels proposed by Claude Code and reviewed by Martins Ogiekpolor (3 Nov 2026)."
)


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    centre = (p + Z * Z / (2 * n)) / (1 + Z * Z / n)
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / (1 + Z * Z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def label(r: dict) -> str:
    if r.get("confirmed_by_martins") == "no" and r.get("martins_label"):
        return r["martins_label"]
    return r["proposed_label"]


def is_unsure(r: dict) -> bool:
    return "unsure" in (r["proposed_label"], r.get("confirmed_by_martins"), label(r))


def volume_share_ci(rows: list[dict], good: str, rng: random.Random) -> tuple[float, float]:
    stats = []
    for _ in range(BOOT):
        s = [rows[rng.randrange(len(rows))] for _ in rows]
        total = sum(int(r["complaints"]) for r in s)
        stats.append(sum(int(r["complaints"]) for r in s if label(r) == good) / total)
    stats.sort()
    return (stats[int(0.025 * BOOT)], stats[int(0.975 * BOOT) - 1])


def main() -> dict:
    rows = list(csv.DictReader(open(DIR / "e01_match_labels.csv", encoding="utf-8")))
    pop = {p["match_method"]: p for p in json.loads((DIR / "e01_population.json").read_text())["population"]}
    matched_pop_co = sum(p["companies"] for m, p in pop.items() if m != "unmatched")
    matched_pop_vol = sum(p["complaints"] for m, p in pop.items() if m != "unmatched")
    unmatched_pop_co, unmatched_pop_vol = pop["unmatched"]["companies"], pop["unmatched"]["complaints"]
    rng = random.Random(SEED)

    matched = [r for r in rows if r["group"] == "matched"]
    m_ok = [r for r in matched if not is_unsure(r)]
    correct = [r for r in m_ok if label(r) == "correct"]
    vol = lambda rs: sum(int(r["complaints"]) for r in rs)  # noqa: E731
    prec_co = len(correct) / len(m_ok)
    prec_vol = vol(correct) / vol(m_ok)

    top = [r for r in rows if r["group"] == "top_unmatched"]
    top_names = {r["company"] for r in top}
    rest = [r for r in rows if r["group"] == "unmatched" and r["company"] not in top_names]
    rest_ok = [r for r in rest if not is_unsure(r)]
    top_ok = [r for r in top if not is_unsure(r)]
    misses_top = [r for r in top_ok if label(r) == "true_miss"]
    misses_rest = [r for r in rest_ok if label(r) == "true_miss"]
    rest_pop_co = unmatched_pop_co - len(top)
    rest_pop_vol = unmatched_pop_vol - vol(top)
    rest_rate = len(misses_rest) / len(rest_ok)
    rest_rate_hi = wilson(len(misses_rest), len(rest_ok))[1]
    rest_vol_rate = vol(misses_rest) / vol(rest_ok)

    good_vol = prec_vol * matched_pop_vol
    missed_vol = vol(misses_top) + rest_vol_rate * rest_pop_vol
    missed_vol_hi = vol(misses_top) + max(rest_vol_rate, rest_rate_hi) * rest_pop_vol
    pv_lo, pv_hi = volume_share_ci(m_ok, "correct", rng)
    recall_vol = good_vol / (good_vol + missed_vol)
    recall_vol_lo = pv_lo * matched_pop_vol / (pv_lo * matched_pop_vol + missed_vol_hi)
    recall_vol_hi = pv_hi * matched_pop_vol / (pv_hi * matched_pop_vol + vol(misses_top))

    good_co = prec_co * matched_pop_co
    missed_co = len(misses_top) + rest_rate * rest_pop_co
    missed_co_hi = len(misses_top) + rest_rate_hi * rest_pop_co
    pc_lo, pc_hi = wilson(len(correct), len(m_ok))
    recall_co = good_co / (good_co + missed_co)
    recall_co_lo = pc_lo * matched_pop_co / (pc_lo * matched_pop_co + missed_co_hi)
    recall_co_hi = pc_hi * matched_pop_co / (pc_hi * matched_pop_co + len(misses_top))

    # unmatched volume by type: top 50 exactly, the rest scaled from the random sample
    types: dict[str, float] = {}
    for r in top_ok:
        types[label(r)] = types.get(label(r), 0) + int(r["complaints"])
    for r in rest_ok:
        types[label(r)] = types.get(label(r), 0) + int(r["complaints"]) / vol(rest_ok) * rest_pop_vol
    unsure_vol = vol([r for r in top if is_unsure(r)])

    out = {
        "provenance": PROVENANCE,
        "precision": {
            "by_company": {
                "value": prec_co,
                "ci95": wilson(len(correct), len(m_ok)),
                "correct": len(correct),
                "judged": len(m_ok),
                "method": "Wilson",
            },
            "by_volume": {
                "value": prec_vol,
                "ci95": (pv_lo, pv_hi),
                "correct_complaints": vol(correct),
                "judged_complaints": vol(m_ok),
                "method": f"bootstrap over companies, {BOOT} resamples",
            },
            "sample": f"{len(matched)} of {matched_pop_co} matched companies ({len(matched) - len(m_ok)} unsure left out)",
        },
        "recall": {
            "by_volume": {
                "value": recall_vol,
                "range": (recall_vol_lo, recall_vol_hi),
                "matched_correct_complaints_est": round(good_vol),
                "missed_complaints_est": round(missed_vol),
                "missed_complaints_upper": round(missed_vol_hi),
            },
            "by_company": {
                "value": recall_co,
                "range": (recall_co_lo, recall_co_hi),
                "matched_correct_companies_est": round(good_co, 1),
                "missed_companies_est": round(missed_co, 1),
                "missed_companies_upper": round(missed_co_hi, 1),
            },
            "true_misses_top50": [
                (r["company"], int(r["complaints"]), r["proposed_cert"]) for r in misses_top
            ],
            "true_misses_random_rest": len(misses_rest),
            "random_rest_judged": len(rest_ok),
            "rest_population": {"companies": rest_pop_co, "complaints": rest_pop_vol},
        },
        "top50_share_of_unmatched_volume": vol(top) / unmatched_pop_vol,
        "unmatched_volume_by_type": {k: round(v) for k, v in sorted(types.items(), key=lambda kv: -kv[1])},
        "unmatched_volume_unsure": unsure_vol,
        "dedup": [r["company"] for r in rows if r["group"] == "unmatched" and r["company"] in top_names],
    }
    (DIR / "e01_metrics.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    return out


if __name__ == "__main__":
    res = main()
    p, rc = res["precision"], res["recall"]
    pct = lambda x: f"{100 * x:.1f}%"  # noqa: E731
    print(res["provenance"])
    print(
        f"Precision by company: {pct(p['by_company']['value'])} (95% {pct(p['by_company']['ci95'][0])}-{pct(p['by_company']['ci95'][1])}), {p['by_company']['correct']}/{p['by_company']['judged']}; {p['sample']}"
    )
    print(
        f"Precision by volume:  {pct(p['by_volume']['value'])} (95% {pct(p['by_volume']['ci95'][0])}-{pct(p['by_volume']['ci95'][1])})"
    )
    print(
        f"Recall by volume:     {pct(rc['by_volume']['value'])} (range {pct(rc['by_volume']['range'][0])}-{pct(rc['by_volume']['range'][1])}); missed ~{rc['by_volume']['missed_complaints_est']:,} (up to {rc['by_volume']['missed_complaints_upper']:,})"
    )
    print(
        f"Recall by company:    {pct(rc['by_company']['value'])} (range {pct(rc['by_company']['range'][0])}-{pct(rc['by_company']['range'][1])})"
    )
    print(
        "True misses (top 50):",
        rc["true_misses_top50"],
        "| in random rest:",
        rc["true_misses_random_rest"],
        "of",
        rc["random_rest_judged"],
    )
    print(f"Top 50 share of unmatched volume: {pct(res['top50_share_of_unmatched_volume'])}")
    print(
        "Unmatched volume by type:",
        res["unmatched_volume_by_type"],
        "| unsure:",
        res["unmatched_volume_unsure"],
    )
    print("Counted once (in both lists):", res["dedup"])
