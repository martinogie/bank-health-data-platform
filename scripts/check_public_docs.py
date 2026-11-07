"""Before anything is published: prove the files hold no account identifier, hostname, address, key
or personal path (Phase 4, PR 11; decision 6). The rules live in ingestion/public_scan.py, which the
site publish DAG also runs before every upload.

    .venv\\Scripts\\python scripts/check_public_docs.py site/public/data/*.json [more files]

Generic patterns, plus the real values from the environment (.env) at run time, never printed: a
hit shows only the variable's name. Exit 0 = clean; 1 = something found.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the repo root, for `ingestion`

from ingestion.public_scan import ENV_NAMES, PATTERNS, env_values, scan  # noqa: E402, F401


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
