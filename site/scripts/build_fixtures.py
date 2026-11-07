"""Stage A: build the website's preview data (site/public/data/) from real data published on 6 Nov 2026.

The files are built by ingestion/site_export.py, the same code the weekly publish DAG uses; this
script only feeds it the saved inputs and writes the result with "fixture": true (the site then
shows a "Preview data" label). No hand-written text about any bank.

Inputs (site/.fixture-inputs/, git-ignored, pulled once from prod): watchlist.json + waterfalls/
(schema 5), snowflake_inputs.json (read-only extracts: quarterly net income for the preview banks,
the latest successful run, OPS.HEALTH_RUNS_LAST_12), preview_certs.json (the banks that get a full
file), airflow_attempts.json (the two resumed runs logged before record_run kept their attempts).

    python site/scripts/build_fixtures.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SITE.parent))  # the repo root, for `ingestion`

from ingestion.site_export import build_site, encode, load_model  # noqa: E402

IN = SITE / ".fixture-inputs"
OUT = SITE / "public" / "data"


def main() -> None:
    read = lambda name: json.loads((IN / name).read_text(encoding="utf-8"))  # noqa: E731
    watchlist, sf = read("watchlist.json"), read("snowflake_inputs.json")
    waterfalls = {
        int(f.stem): json.loads(f.read_text(encoding="utf-8")) for f in (IN / "waterfalls").glob("*.json")
    }
    attempts = read("airflow_attempts.json") if (IN / "airflow_attempts.json").exists() else {}
    docs = build_site(
        watchlist, waterfalls, sf["ni"], sf["run"][0], sf["health"], load_model(),
        fixture=True, preview=set(read("preview_certs.json")),
        resumed_overrides={k: v for k, v in attempts.items() if not k.startswith("_")},
    )  # fmt: skip
    if OUT.exists():
        shutil.rmtree(OUT)
    for rel, data in encode(docs).items():
        (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
        (OUT / rel).write_bytes(data)
    n = sum(1 for p in docs if p.startswith("banks/"))
    print(
        f"fixtures: {len(watchlist['banks'])} banks in the index, {n} bank files, top 20, model, pipeline -> {OUT}"
    )


if __name__ == "__main__":
    main()
