"""Prove files hold no account identifier, hostname, address, key or personal path before they are
published (Phase 4, PR 11; decision 6). Used by the site publish DAG (every data file and the dbt
docs, before upload) and by scripts/check_public_docs.py (CI and local checks).

Two kinds of checks:
- generic patterns, so this public module names nothing (email addresses, IPv4 addresses, OCI IDs,
  Snowflake and Databricks hostnames, private-key headers, local home paths, AWS keys);
- the real values from the environment at run time (SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER,
  DATABRICKS_HOST, PROD_HOST_ID, DATABRICKS_JOB_ID, and the R2 endpoint's host and account ID),
  never printed: a hit shows only the variable's name.
"""

from __future__ import annotations

import os
import re

PATTERNS = {
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?:com|net|org|io|edu|gov|co|ai)\b"),
    "IPv4 address": re.compile(
        r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])"
    ),
    "OCI identifier": re.compile(r"ocid1\.[a-z0-9]+\.oc\d"),
    "Snowflake hostname": re.compile(r"[A-Za-z0-9-]+\.snowflakecomputing\.com", re.I),
    "Databricks hostname": re.compile(
        r"[A-Za-z0-9-]+\.(?:cloud\.databricks\.com|azuredatabricks\.net)", re.I
    ),
    "R2 endpoint": re.compile(r"[A-Za-z0-9-]+\.r2\.cloudflarestorage\.com", re.I),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "home path": re.compile(
        r"(?:[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9._-]+|/home/[a-z][a-z0-9_-]+|/Users/[A-Za-z0-9._-]+)"
    ),
    "AWS key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}
# Real values that must not appear, read from the environment; never printed.
ENV_NAMES = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "DATABRICKS_HOST", "PROD_HOST_ID", "DATABRICKS_JOB_ID"]
# Addresses that are fine in public docs (documentation examples, loopback).
ALLOWED_IPS = {"127.0.0.1", "0.0.0.0"}


def env_values(environ: dict[str, str] | None = None) -> dict[str, str]:
    """{variable name: value} for the real values to look for (values of 4+ characters only)."""
    env = os.environ if environ is None else environ
    out = {}
    for name in ENV_NAMES:
        value = (env.get(name) or "").strip()
        if name == "DATABRICKS_HOST":
            value = value.removeprefix("https://").rstrip("/")
        if len(value) >= 4 and value.lower() != "placeholder":
            out[name] = value
    endpoint = (env.get("R2_ENDPOINT") or "").strip().removeprefix("https://").rstrip("/")
    if endpoint:
        out["R2_ENDPOINT"] = endpoint
        account = endpoint.split(".", 1)[0]
        if len(account) >= 8:
            out["R2 account ID"] = account
    return out


def scan(text: str, secrets: dict[str, str]) -> list[str]:
    """Findings in `text` (rule name and a masked hint; real values are named, never shown)."""
    hits = []
    for rule, pattern in PATTERNS.items():
        for m in pattern.finditer(text):
            if rule == "IPv4 address" and m.group(0) in ALLOWED_IPS:
                continue
            found = m.group(0)
            masked = found[:3] + "…" + found[-2:] if len(found) > 6 else "…"
            hits.append(f"{rule}: {masked}")
    lowered = text.lower()
    for name, value in secrets.items():
        n = lowered.count(value.lower())
        if n:
            hits.append(f"value of {name} ({n}x)")
    return hits


def scan_files(files: dict[str, bytes], secrets: dict[str, str] | None = None) -> dict[str, list[str]]:
    """{path: findings} for every file with at least one finding; {} means all clean."""
    secrets = env_values() if secrets is None else secrets
    out = {}
    for path, data in files.items():
        hits = scan(data.decode("utf-8", errors="replace"), secrets)
        if hits:
            out[path] = sorted(set(hits))
    return out
