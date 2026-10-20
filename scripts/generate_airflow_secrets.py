"""Add Airflow's two internal random keys to .env (run once, by you).

  AIRFLOW__CORE__FERNET_KEY      encrypts connection passwords in Airflow's DB
  AIRFLOW__API_AUTH__JWT_SECRET  signs tokens between Airflow's own services

Values are written straight into .env and never printed. Existing non-empty
values are left alone, so it is safe to run again.

Usage (from the project folder):  python scripts/generate_airflow_secrets.py
"""
import base64
import os
import re
import secrets
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"

GENERATORS = {
    # A Fernet key is 32 random bytes, url-safe base64 encoded.
    "AIRFLOW__CORE__FERNET_KEY": lambda: base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "AIRFLOW__API_AUTH__JWT_SECRET": lambda: secrets.token_urlsafe(48),
}


def main() -> None:
    if not ENV.exists():
        raise SystemExit(f"{ENV} not found. First copy .env.example to .env.")
    text = ENV.read_text(encoding="utf-8")
    changed = []
    for name, make in GENERATORS.items():
        pattern = re.compile(rf"^{name}=[ \t]*(?:#.*)?$", re.MULTILINE)  # present but empty
        has_value = re.search(rf"^{name}=[ \t]*[^\s#]", text, re.MULTILINE)
        if has_value:
            continue
        line = f"{name}={make()}"
        if pattern.search(text):
            text = pattern.sub(line, text, count=1)
        else:
            text = text.rstrip("\n") + "\n" + line + "\n"
        changed.append(name)
    ENV.write_text(text, encoding="utf-8")
    print("Set:", ", ".join(changed) if changed else "nothing (already set)")


if __name__ == "__main__":
    main()
