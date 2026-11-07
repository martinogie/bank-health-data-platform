"""Publish the website's data and dbt docs to Cloudflare R2 (private bucket), one run at a time.

Layout in the bucket (prod at the root; dev under "dev/", which the site never serves):
    runs/<run key>/data/overview.json, banks_index.json, model.json, pipeline.json, banks/<cert>.json
    runs/<run key>/dbt/index.html, manifest.json, catalog.json
    current.json   {"run": "<run key>", "publishedAt", "quarter", "files"}  <- the router reads this

Order: validate every file against the JSON Schemas -> scan every file for identifiers and secrets
-> upload to a NEW prefix -> check every object is there -> switch current.json -> delete runs
beyond the newest KEEP (never the current one). A failure before the switch leaves the live site on
the previous run. Rollback = point current.json at an earlier run:

    python -m ingestion.site_publish list
    python -m ingestion.site_publish rollback <run key>

Credentials (R2 token: object read and write on this bucket only) come from the environment:
R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT (from OCI Vault in prod, .env in dev). Values
are never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ingestion.public_scan import scan_files
from ingestion.site_export import CONTRACT_DIR

# The Airflow asset bank_health_weekly's record_run emits on success; bank_health_publish_site runs on it.
WEEKLY_SUCCESS_ASSET = "bank_health_weekly_success"
BUCKET = os.environ.get("R2_BUCKET", "bankhealth-data")
KEEP = 5  # the current run plus at least the previous 4 weeks
POINTER = "current.json"
RUN_KEY = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
DBT_FILES = ("index.html", "manifest.json", "catalog.json")
TYPES = {".json": "application/json; charset=utf-8", ".html": "text/html; charset=utf-8"}


class PublishRefused(RuntimeError):
    """A check failed before anything went live; the site keeps the previous run."""


# ------------------------------------------------------------------ checks
def _schema_validators() -> dict[str, Any]:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    schemas = {
        p.name: json.loads(p.read_text(encoding="utf-8")) for p in (CONTRACT_DIR / "schemas").glob("*.json")
    }
    registry = Registry().with_resources((name, Resource.from_contents(s)) for name, s in schemas.items())
    files = schemas["files.schema.json"]

    def v(defn: str) -> Any:
        return Draft202012Validator({"$ref": f"files.schema.json#/$defs/{defn}"}, registry=registry)

    return {"overview": v("overview"), "banksIndex": v("banksIndex"), "model": v("model"),
            "pipeline": v("pipeline"), "bank": v("bank"), "_defs": sorted(files["$defs"])}  # fmt: skip


def validate(docs: dict[str, dict]) -> list[str]:
    """Schema problems as 'path: message' (empty = valid). Every expected file must be present."""
    vs = _schema_validators()
    which = {"overview.json": "overview", "banks_index.json": "banksIndex", "model.json": "model",
             "pipeline.json": "pipeline"}  # fmt: skip
    problems = [f"{name}: missing" for name in which if name not in docs]
    banks = [p for p in docs if p.startswith("banks/")]
    if not banks:
        problems.append("banks/: no bank files")
    for path, doc in docs.items():
        defn = which.get(path) or ("bank" if path.startswith("banks/") else None)
        if defn is None:
            problems.append(f"{path}: not a site data file")
            continue
        for err in vs[defn].iter_errors(doc):
            loc = "/".join(str(p) for p in err.absolute_path) or "(top)"
            problems.append(f"{path}: {loc}: {err.message[:160]}")
            if len(problems) > 50:
                return problems
    return problems


def check(files: dict[str, bytes], docs: dict[str, dict]) -> None:
    """Validate and scan, or raise PublishRefused listing what failed (no values shown)."""
    problems = validate(docs)
    if problems:
        raise PublishRefused(f"{len(problems)} schema problem(s): " + "; ".join(problems[:10]))
    hits = scan_files(files)
    if hits:
        listed = "; ".join(f"{p}: {', '.join(h)}" for p, h in list(hits.items())[:10])
        raise PublishRefused(f"identifier scan: {len(hits)} file(s) not clean: {listed}")


def dbt_docs(target: Path) -> dict[str, bytes]:
    """The dbt docs site from dbt/target (dbt docs generate), or {} if it isn't there."""
    if not all((target / f).is_file() for f in DBT_FILES):
        return {}
    return {f"dbt/{f}": (target / f).read_bytes() for f in DBT_FILES}


# ------------------------------------------------------------------ R2
def client():
    import boto3
    from botocore.config import Config

    for name in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT"):
        if not os.environ.get(name):
            raise PublishRefused(f"{name} is not set (R2 token from OCI Vault in prod, .env in dev)")
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 5, "mode": "standard"}, max_pool_connections=16),
    )


def run_key(run_id: str, started_at: Any) -> str:
    """Sortable, unique key for a run: <started UTC>Z-<6 hex of the run id>."""
    t = (
        started_at
        if isinstance(started_at, datetime)
        else datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    )
    t = (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC)
    return f"{t:%Y%m%dT%H%M%S}Z-{hashlib.sha256(run_id.encode()).hexdigest()[:6]}"


def _list_keys(s3, prefix: str) -> list[str]:
    keys, token = [], None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": prefix, **({"ContinuationToken": token} if token else {})}
        page = s3.list_objects_v2(**kw)
        keys += [o["Key"] for o in page.get("Contents", [])]
        if not page.get("IsTruncated"):
            return keys
        token = page["NextContinuationToken"]


def list_runs(s3, root: str = "") -> list[str]:
    """Run keys in the bucket, oldest first."""
    seen = {k[len(root) + 5 :].split("/", 1)[0] for k in _list_keys(s3, f"{root}runs/")}
    return sorted(k for k in seen if RUN_KEY.match(k))


def read_pointer(s3, root: str = "") -> dict | None:
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=f"{root}{POINTER}")["Body"].read())
    except Exception as exc:  # NoSuchKey on the first publish
        if "NoSuchKey" in type(exc).__name__ or "NoSuchKey" in str(exc) or "404" in str(exc):
            return None
        raise


def write_pointer(s3, root: str, key: str, extra: dict | None = None) -> dict:
    doc = {"run": key, "publishedAt": datetime.now(UTC).isoformat(timespec="seconds"), **(extra or {})}
    s3.put_object(Bucket=BUCKET, Key=f"{root}{POINTER}", Body=json.dumps(doc).encode(),
                  ContentType=TYPES[".json"], CacheControl="no-cache")  # fmt: skip
    return doc


def upload(s3, root: str, key: str, files: dict[str, bytes]) -> int:
    """Upload every file under <root>runs/<key>/, then check every object is there."""
    base = f"{root}runs/{key}/"

    def put(item: tuple[str, bytes]) -> None:
        path, data = item
        s3.put_object(Bucket=BUCKET, Key=base + path, Body=data, ContentType=TYPES.get(Path(path).suffix,
                      "application/octet-stream"), CacheControl="public, max-age=300")  # fmt: skip

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(put, files.items()))
    present = set(_list_keys(s3, base))
    missing = [p for p in files if base + p not in present]
    if missing:
        raise PublishRefused(
            f"{len(missing)} object(s) missing after upload, e.g. {missing[:3]}; not switching"
        )
    return len(files)


def prune(s3, root: str, current: str, keep: int = KEEP) -> list[str]:
    """Delete runs beyond the newest `keep`; never the current one. Returns the deleted run keys."""
    runs = list_runs(s3, root)
    old = [k for k in runs[:-keep] if k != current] if len(runs) > keep else []
    for k in old:
        keys = _list_keys(s3, f"{root}runs/{k}/")
        for i in range(0, len(keys), 1000):
            s3.delete_objects(
                Bucket=BUCKET, Delete={"Objects": [{"Key": x} for x in keys[i : i + 1000]], "Quiet": True}
            )
    return old


def bundle(docs: dict[str, dict], dbt: dict[str, bytes]) -> dict[str, bytes]:
    """Everything to upload: data/<file> (compact JSON) and dbt/<file>."""
    from ingestion.site_export import encode

    return {**{f"data/{p}": b for p, b in encode(docs).items()}, **dbt}


def publish(s3, root: str, key: str, docs: dict[str, dict], dbt: dict[str, bytes], extra: dict) -> dict:
    """Check, upload, switch the pointer, prune. Returns a summary for logs (no secrets)."""
    files = bundle(docs, dbt)
    check(files, docs)
    previous = (read_pointer(s3, root) or {}).get("run")
    n = upload(s3, root, key, files)
    pointer = write_pointer(s3, root, key, {**extra, "files": n})
    deleted = prune(s3, root, key)
    return {"run": key, "files": n, "previous": previous, "deleted": deleted, "pointer": pointer}


def rollback(s3, root: str, to: str) -> dict:
    if to not in list_runs(s3, root):
        raise PublishRefused(f"run {to} is not in the bucket (python -m ingestion.site_publish list)")
    current = read_pointer(s3, root) or {}
    return write_pointer(
        s3, root, to, {"rolledBackFrom": current.get("run"), "quarter": current.get("quarter")}
    )


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="List the published runs or roll the site back to one.")
    p.add_argument("--dev", action="store_true", help="the dev/ prefix instead of prod")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    rb = sub.add_parser("rollback")
    rb.add_argument("run")
    args = p.parse_args(list(argv) if argv is not None else None)
    root = "dev/" if args.dev else ""
    s3 = client()
    current = (read_pointer(s3, root) or {}).get("run")
    if args.cmd == "list":
        for k in list_runs(s3, root):
            print(k, "<- current" if k == current else "")
        return 0
    doc = rollback(s3, root, args.run)
    print(f"current.json now points at {doc['run']} (was {doc.get('rolledBackFrom')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
