"""Integration-test setup: read .env at runtime (values never printed or stored).

Tests that need credentials skip themselves when those values are missing,
so this folder is safe to run on any machine.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE reader; existing environment variables win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if value and not value.startswith("#"):
            os.environ.setdefault(key.strip(), value)


_load_env_file(ENV_FILE)


def require_env(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.skip(f"needs {', '.join(missing)} in .env")
