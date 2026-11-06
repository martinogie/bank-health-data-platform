"""Validation pack, step 1: do the model card's numbers match what MLflow recorded? (read-only)

Looks up the registered model's alias (default @champion) in Unity Catalog, then compares each
figure in docs/validation/expected_v5.json with the record it says (`where`):
  run_metric   a metric logged by the training run behind that version (test results)
  version_tag  a tag on the model version (band cut-offs: what scoring reads)
A run metric with the same name as a version tag but a different value is reported as a NOTE.
No compute is started and nothing is written: these are metadata API calls with the token in .env.

    .venv\\Scripts\\python scripts/validation_reproduce.py            # exit 0 = every figure matches

Retraining from scratch (step 2 in docs/validation/README.md) is a separate, deliberate dev run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _env import ROOT, load_env

sys.path.insert(0, str(ROOT))
from ingestion import databricks_jobs as dj  # noqa: E402

EXPECTED = ROOT / "docs" / "validation" / "expected_v5.json"


def registered_version(api, model: str, alias: str) -> tuple[str, str, dict[str, str]]:
    """(version, run_id, version tags) behind model@alias."""
    mv = api.call("GET", f"/api/2.1/unity-catalog/models/{model}/aliases/{alias}")
    version = str(mv["version"])
    full = api.call(
        "GET", "/api/2.0/mlflow/unity-catalog/model-versions/get", params={"name": model, "version": version}
    )["model_version"]
    tags = {t["key"]: t["value"] for t in full.get("tags", [])}
    return version, mv["run_id"], tags


def logged_metrics(api, run_id: str) -> dict[str, float]:
    run = api.call("GET", "/api/2.0/mlflow/runs/get", params={"run_id": run_id})["run"]
    return {m["key"]: float(m["value"]) for m in run.get("data", {}).get("metrics", [])}


def compare(expected: dict, metrics: dict[str, float], tags: dict[str, str]) -> list[tuple[str, str, str]]:
    """(status, metric, description); status is ok, FAIL or NOTE. Never raises on a missing figure."""
    rows = []
    for key, spec in expected["metrics"].items():
        where = spec.get("where", "run_metric")
        source = metrics if where == "run_metric" else {k: float(v) for k, v in tags.items() if _number(v)}
        if key not in source:
            rows.append(("FAIL", key, f"no {where} named {key}"))
            continue
        ok = abs(source[key] - spec["expected"]) <= spec["tolerance"]
        rows.append(
            ("ok" if ok else "FAIL", key, f"model card {spec['expected']}, {where} {source[key]:.6f}")
        )
        if where == "version_tag" and key in metrics and abs(metrics[key] - source[key]) > spec["tolerance"]:
            rows.append(
                ("NOTE", key, f"the training run also logged {metrics[key]:.6f} (differs from the tag)")
            )
    return rows


def _number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None, api=None) -> int:
    parser = argparse.ArgumentParser(description="Compare the model card with MLflow (read-only).")
    parser.add_argument("--expected", type=Path, default=EXPECTED)
    args = parser.parse_args(argv)
    expected = json.loads(args.expected.read_text(encoding="utf-8"))
    if api is None:
        load_env()
        api = dj.DatabricksAPI.from_env()
    version, run_id, tags = registered_version(api, expected["model"], expected["alias"])
    print(f"{expected['model']}@{expected['alias']} = version {version} (run {run_id})")
    problems = 0
    if version != expected["version"]:
        print(f"FAIL the card describes version {expected['version']}, the alias points at {version}")
        problems += 1
    for status, key, text in compare(expected, logged_metrics(api, run_id), tags):
        print(f"{status:<4} {key}: {text}")
        problems += status == "FAIL"
    print("all figures match" if not problems else f"{problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
