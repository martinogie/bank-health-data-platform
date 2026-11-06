"""Dev only: simulate PR 8's Vault mode with the DEV secrets (no OCI, no Databricks compute).

Reads the dev .env and writes, into OUT_DIR:
  runtime/env/<NAME>     the same secret files deploy/vault_secrets.py writes into /run/bank-health
  runtime/keys/<file>    the dev Snowflake key, ENCRYPTED with a random passphrase (as in prod),
                         and that passphrase as runtime/env/SNOWFLAKE_PRIVATE_KEY_PASSPHRASE
  config.env             the dev .env WITHOUT those secrets (like prod's .env after the cleanup)
so the dev stack can be started with:
  BANK_HEALTH_ENV_FILE=OUT_DIR/config.env
  BANK_HEALTH_RUNTIME_SECRETS_DIR=OUT_DIR/runtime
  BANK_HEALTH_KEYS_DIR=OUT_DIR/runtime/keys
Nothing is printed except names. Delete OUT_DIR after the test.

    python scripts/dev_runtime_secrets.py <dev .env> <dev keys dir> <OUT_DIR>
"""

from __future__ import annotations

import importlib.util
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vault_secrets", ROOT / "deploy" / "vault_secrets.py")
assert spec and spec.loader
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)

ENV_NAMES = [target for kind, target in vs.SECRETS.values() if kind == "env"]


def main(env_path: Path, keys_dir: Path, out: Path) -> None:
    env = vs.parse_env_file(env_path)
    runtime = out / "runtime"
    (runtime / "env").mkdir(parents=True, exist_ok=False)
    (runtime / "keys").mkdir()

    key_name = Path(env["SNOWFLAKE_PRIVATE_KEY_PATH"]).name
    old_pass = env.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or None
    key = serialization.load_pem_private_key(
        (keys_dir / key_name).read_bytes(), password=old_pass.encode() if old_pass else None
    )
    new_pass = secrets.token_urlsafe(24)
    (runtime / "keys" / key_name).write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(new_pass.encode()),
        )
    )
    values = {name: env.get(name, "") for name in ENV_NAMES}
    values["SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"] = new_pass
    if not values.get("AIRFLOW_DB_PASSWORD"):  # dev has none: a random one (letters and digits)
        values["AIRFLOW_DB_PASSWORD"] = secrets.token_hex(16)
    missing = [n for n, v in values.items() if not v.strip()]
    for name, value in values.items():
        if value.strip():
            (runtime / "env" / name).write_text(value.strip(), encoding="utf-8", newline="\n")

    kept = [
        line
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if line.split("=", 1)[0].strip() not in ENV_NAMES
    ]
    (out / "config.env").write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    print(f"runtime/env: {sorted(p.name for p in (runtime / 'env').iterdir())}")
    print(f"runtime/keys: {key_name} (encrypted, new random passphrase)")
    print(f"config.env: {len(kept)} lines, secrets removed")
    if missing:
        print(f"not in the dev .env (left out): {missing}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
