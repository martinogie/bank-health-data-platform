r"""E-01: draw the labelled sample of CFPB company -> FDIC bank matches.

    .venv\Scripts\python scripts/e01_match_sample.py            (dev .env; reads prod silver, read-only)

Runs databricks/notebooks/tools/e01_match_sample (one small serverless run) and writes
  docs/investigations/e01_match_labels.csv      one row per sampled company, with evidence and
                                                 empty label columns
  docs/investigations/e01_population.json       companies and complaints by match method
It never overwrites an existing labels file: labels are typed in by hand (proposed_label by the
assistant, confirmed_by_martins / martins_label by Martins). Delete or move the file to redraw.
"""

from __future__ import annotations

import csv
import json
import sys

from _env import ROOT, load_env

sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "docs" / "investigations"
LABELS = OUT_DIR / "e01_match_labels.csv"
POPULATION = OUT_DIR / "e01_population.json"
EVIDENCE = [
    "group", "draw_rank", "company", "complaints", "match_method", "cert", "matched_bank_name",
    "matched_holding_company", "matched_state", "matched_active", "matched_assets_k", "top_states",
    "top_products", "candidate_name", "candidate_kind", "candidate_cert", "candidate_state",
    "candidate_active", "candidate_similarity",
]  # fmt: skip
LABEL_COLUMNS = [
    "proposed_label",
    "proposed_cert",
    "evidence_note",
    "confirmed_by_martins",
    "martins_label",
    "martins_note",
]


def write_labels(rows: list[dict], path=LABELS) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EVIDENCE + LABEL_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {**{c: "" for c in LABEL_COLUMNS}, **{k: ("" if v is None else v) for k, v in r.items()}}
            )


def main() -> int:
    if LABELS.exists():
        print(
            f"{LABELS} exists (it may hold labels); not overwriting. Move it away to redraw.", file=sys.stderr
        )
        return 1
    load_env()
    from databricks_run import run_notebook

    from ingestion import databricks_jobs as dj
    from ingestion import environment

    s = environment.settings()
    db = dj.DatabricksAPI.from_env()
    repo_path = dj.sync_notebooks(db, s)
    outputs: dict = {}
    params = {"catalog": environment.databricks_catalog(), "env": s.name, "source_catalog": "bank_health"}
    if not run_notebook(db, repo_path, "tools/e01_match_sample", params, outputs=outputs):
        raise SystemExit("tools/e01_match_sample failed")
    result = outputs["tools/e01_match_sample"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_labels(result["sample"])
    POPULATION.write_text(
        json.dumps({k: result[k] for k in ("source_catalog", "seed", "population")}, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {LABELS} ({len(result['sample'])} rows) and {POPULATION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
