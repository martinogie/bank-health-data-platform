"""Before anything is published (dbt docs at bank.martinsogiekpolor.com/dbt): prove the files hold
no account identifier, hostname, address, key or data (Phase 4, PR 11; decision 6).

    .venv\\Scripts\\python scripts/check_public_docs.py site/dbt/index.html [more files]

Two kinds of checks:
- generic patterns, so the public script itself names nothing (email addresses, IPv4 addresses,
  OCI IDs, Snowflake and Databricks hostnames, private-key headers, local home paths, AWS keys);
- the real values from .env at run time (SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, DATABRICKS_HOST,
  PROD_HOST_ID, ...), never printed: a hit shows only the variable's name.
Exit 0 = clean; 1 = something found (each hit shows the rule and a short masked context).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

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
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "home path": re.compile(
        r"(?:[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9._-]+|/home/[a-z][a-z0-9_-]+|/Users/[A-Za-z0-9._-]+)"
    ),
    "AWS key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}
# Real values that must not appear, read from the environment (.env); never printed.
ENV_NAMES = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "DATABRICKS_HOST", "PROD_HOST_ID", "DATABRICKS_JOB_ID"]
# Addresses that are fine in public docs (documentation examples, loopback).
ALLOWED_IPS = {"127.0.0.1", "0.0.0.0"}


def env_values() -> dict[str, str]:
    out = {}
    for name in ENV_NAMES:
        value = (os.environ.get(name) or "").strip()
        if name == "DATABRICKS_HOST":
            value = value.removeprefix("https://").rstrip("/")
        if len(value) >= 4 and value.lower() != "placeholder":
            out[name] = value
    return out


def scan(text: str, secrets: dict[str, str]) -> list[str]:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check files before publishing them.")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    secrets = env_values()
    print(
        f"checking {len(args.files)} file(s); {len(PATTERNS)} patterns; real values from .env: {sorted(secrets) or 'none set'}"
    )
    total = 0
    for path in args.files:
        hits = scan(path.read_text(encoding="utf-8", errors="replace"), secrets)
        total += len(hits)
        print(f"{path.name}: {'clean' if not hits else f'{len(hits)} hit(s)'}")
        for h in sorted(set(hits)):
            print("  ", h, f"(x{hits.count(h)})" if hits.count(h) > 1 else "")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
