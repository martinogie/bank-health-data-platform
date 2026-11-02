"""The prod dbt manifest, which dbt slim CI compares a pull request against.

dbt writes target/manifest.json: every model, test and source with a checksum of its SQL and
config. CI runs `dbt build --select state:modified+ --defer --state <prod manifest>`, so it
builds only what a pull request changed (plus everything downstream of it), and reads the
unchanged models straight from prod.

Where the manifest lives: the Snowflake stage BANK_HEALTH.RAW.DBT_ARTIFACTS
(snowflake/setup_dev.sql). Prod Airflow uploads it after every successful dbt build
(task publish_dbt_manifest), so the stage always holds the manifest of the latest successful
prod run. CI_ROLE may only read the stage. Prod dbt runs in Airflow, not in GitHub Actions,
so a GitHub artifact would need a GitHub token on the prod host; the stage needs nothing new.

Command line (used by .github/workflows/ci.yml):
    python -m ingestion.dbt_manifest fetch --out prod-manifest
    exit code 0 and prints the path if found; exit code 3 if the stage is empty (no prod run yet)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ingestion.environment import PROD_DATABASE, assert_can_publish

log = logging.getLogger(__name__)

STAGE = f"{PROD_DATABASE}.RAW.DBT_ARTIFACTS"
MANIFEST = "manifest.json"
NOT_FOUND_EXIT_CODE = 3


BUILD_SCHEMA = "MARTS_NEXT"  # where the pipeline builds the marts before publishing (marts_publish)


def check_manifest(path: Path) -> dict:
    """Read a manifest and return a short summary; raises ValueError if it isn't one, or if any
    node points at MARTS_NEXT. Slim CI defers to these relations, and MARTS_NEXT holds last week's
    marts after a publish (and is replaced by the next run), so the manifest must name MARTS."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "nodes" not in data or "metadata" not in data:
        raise ValueError(f"{path} is not a dbt manifest (no nodes/metadata)")
    staged = sorted(
        node_id
        for node_id, node in data["nodes"].items()
        if isinstance(node, dict)
        and (
            str(node.get("schema", "")).upper() == BUILD_SCHEMA
            or f".{BUILD_SCHEMA}." in str(node.get("relation_name", "")).replace('"', "").upper()
        )
    )
    if staged:
        raise ValueError(
            f"{path} points {len(staged)} node(s) at {BUILD_SCHEMA}, not the published MARTS "
            f"(e.g. {staged[0]}); slim CI would defer to last week's marts. Regenerate it without "
            "var marts_schema."
        )
    meta = data["metadata"]
    return {
        "nodes": len(data["nodes"]),
        "dbt_version": meta.get("dbt_version"),
        "generated_at": meta.get("generated_at"),
    }


def _file_url(path: Path) -> str:
    # PUT/GET want file:// URLs with forward slashes, also on Windows (file://C:/...).
    return "file://" + Path(path).resolve().as_posix()


def publish_manifest(conn, manifest_path: Path) -> dict:
    """Upload the prod manifest to the stage. Prod only."""
    from ingestion.snowflake_load import check_target_database

    assert_can_publish("the prod dbt manifest")
    check_target_database(conn)
    summary = check_manifest(manifest_path)
    if Path(manifest_path).name != MANIFEST:
        raise ValueError(f"expected a file named {MANIFEST}, got {manifest_path}")
    cur = conn.cursor()
    try:
        cur.execute(f"PUT '{_file_url(manifest_path)}' @{STAGE} AUTO_COMPRESS=FALSE OVERWRITE=TRUE")
    finally:
        cur.close()
    log.info(
        "published %s (%s nodes, generated %s) to @%s",
        MANIFEST,
        summary["nodes"],
        summary["generated_at"],
        STAGE,
    )
    return {"stage": STAGE, **summary}


def fetch_manifest(conn, out_dir: Path) -> Path | None:
    """Download the prod manifest into out_dir. Returns its path, or None if the stage is empty."""
    cur = conn.cursor()
    try:
        cur.execute(f"LIST @{STAGE} PATTERN = '.*{MANIFEST}'")
        if not cur.fetchall():
            return None
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cur.execute(f"GET @{STAGE}/{MANIFEST} '{_file_url(out_dir)}/'")
    finally:
        cur.close()
    path = out_dir / MANIFEST
    check_manifest(path)
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="download the prod manifest from the stage")
    fetch.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    logging.getLogger("snowflake").setLevel(logging.ERROR)  # login URLs contain the account
    from ingestion.snowflake_load import connect_from_env

    conn = connect_from_env()
    try:
        path = fetch_manifest(conn, args.out)
    finally:
        conn.close()
    if path is None:
        print(f"no {MANIFEST} in @{STAGE} yet", file=sys.stderr)
        return NOT_FOUND_EXIT_CODE
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
