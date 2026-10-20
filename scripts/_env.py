"""Read KEY=VALUE lines from the project's .env into os.environ (values never printed)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.split(" #", 1)[0].strip().strip('"').strip("'")
            if value and not value.startswith("#"):
                os.environ.setdefault(key.strip(), value)
