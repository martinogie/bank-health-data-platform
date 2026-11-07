#!/usr/bin/env python3
"""Fetch the prod secrets from OCI Vault into RAM (/run/bank-health), as the VM itself (Phase 4, PR 8).

Runs on the VM as the deploy user, in /srv/bank-health/venv (oci SDK, deploy/requirements-vault.txt):

    vault_secrets.py fetch                # Vault -> /run/bank-health/{env,keys}/ (RAM only, mode 600)
    vault_secrets.py check-running        # do Vault's values equal what the running Airflow uses?

Auth is the instance principal: the dynamic group bank-health-vm-dg and the policy
bank-health-vm-read-secrets let this VM read secret bundles from bank-health-vault only. No key or
token is stored on the machine. The vault OCID is not secret but stays out of the public repo: it
is BANK_HEALTH_VAULT_ID in /srv/bank-health/app/airflow/.env (git-ignored, Compose settings).

Rules: every secret is read before anything is written (a failure leaves the old files as they
were); files are replaced atomically; values are never printed or logged, only names, OK/FAILED,
and for check-running "matches" / "DIFFERS" (SHA-256 fingerprints are compared, never shown).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

RUNTIME_DIR = Path("/run/bank-health")
COMPOSE_ENV = Path("/srv/bank-health/app/airflow/.env")

# Vault secret name -> where it goes. ("env", NAME) becomes env/NAME, exported by the container
# entrypoint (airflow/include/load_secrets.sh, which lists the same names); ("file", PATH) is
# written as PATH under the runtime dir.
SECRETS: dict[str, tuple[str, str]] = {
    "prod-databricks-token": ("env", "DATABRICKS_TOKEN"),
    "prod-snowflake-key-passphrase": ("env", "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
    "prod-airflow-fernet-key": ("env", "AIRFLOW__CORE__FERNET_KEY"),
    "prod-airflow-jwt-secret": ("env", "AIRFLOW__API_AUTH__JWT_SECRET"),
    "prod-airflow-admin-password": ("env", "_AIRFLOW_WWW_USER_PASSWORD"),
    "prod-slack-webhook": ("env", "SLACK_WEBHOOK_URL"),
    "prod-discord-webhook": ("env", "DISCORD_WEBHOOK_URL"),
    "prod-airflow-db-password": ("env", "AIRFLOW_DB_PASSWORD"),  # read by airflow/include/db_conn.sh
    "prod-snowflake-private-key": ("file", "keys/snowflake_rsa_key.p8"),
}
# Optional: fetched and exported when present in Vault, skipped (never blocking) when not. Only the
# site publish DAG uses them, so a missing R2 token can never stop Airflow or the weekly run.
OPTIONAL_SECRETS: dict[str, tuple[str, str]] = {
    "prod-r2-access-key-id": ("env", "R2_ACCESS_KEY_ID"),
    "prod-r2-secret-access-key": ("env", "R2_SECRET_ACCESS_KEY"),
    "prod-r2-endpoint": ("env", "R2_ENDPOINT"),
}
SCHEDULER = "airflow-airflow-scheduler-1"

Reader = Callable[[str], str]  # secret name -> value
Fingerprint = Callable[[str], str]  # env var name -> SHA-256 of its value in the running scheduler


class FetchError(RuntimeError):
    pass


def read_all(read: Reader) -> dict[str, str]:
    """Every secret, or FetchError naming the ones that failed (never their values)."""
    values, failed = {}, []
    for name in SECRETS:
        try:
            value = read(name)
        except Exception as exc:  # the SDK raises many types; only the name and type are reported
            failed.append(f"{name} ({type(exc).__name__})")
            continue
        if not value.strip():
            failed.append(f"{name} (empty)")
            continue
        values[name] = value
    if failed:
        raise FetchError("could not read: " + ", ".join(failed))
    for name in OPTIONAL_SECRETS:  # missing or empty: skipped, never a failure
        try:
            value = read(name)
        except Exception:
            continue
        if value.strip():
            values[name] = value
    return values


def _present(values: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Every required secret, plus the optional ones that were found."""
    return {**SECRETS, **{n: t for n, t in OPTIONAL_SECRETS.items() if n in values}}


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    os.replace(tmp, path)


def write_runtime(values: dict[str, str], runtime_dir: Path) -> list[str]:
    """Write the values under runtime_dir; returns the relative paths written."""
    written = []
    for name, (kind, target) in _present(values).items():
        value = values[name]
        if kind == "env":
            rel = f"env/{target}"
            content = value.strip()  # a stray newline from copy-paste must not end up in a token
        else:
            rel = target
            content = value if value.endswith("\n") else value + "\n"
        _write_atomic(runtime_dir / rel, content)
        written.append(rel)
    return written


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines of a .env file (no shell expansion; surrounding quotes removed)."""
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def compare_with_running(values: dict[str, str], running: Fingerprint) -> list[tuple[str, str]]:
    """(secret name, result) per secret: does Vault equal what the running Airflow uses?

    Compares SHA-256 fingerprints (Vault value vs the scheduler process's variable). This replaced
    check-env (comparing with the .env file): Compose drops text after a value on the same line,
    which made check-env report a false DIFFERS on 5 Nov 2026."""
    results = []
    for name, (kind, target) in _present(values).items():
        if kind != "env":
            results.append((name, f"{target}: a file, not compared"))
            continue
        vault = hashlib.sha256(values[name].strip().encode()).hexdigest()
        results.append((name, f"matches {target}" if running(target) == vault else f"DIFFERS from {target}"))
    return results


# Runs inside the scheduler container: the fingerprint of one variable in the RUNNING scheduler
# process (/proc/<pid>/environ). Not `docker exec ... "$VAR"`: a new exec shell only sees Docker's
# stored settings (the old .env), not what the entrypoint exported from Vault (found 6 Nov 2026).
_PROC_FINGERPRINT = """
import hashlib, os, sys
var = sys.argv[1]
pids = []
for d in os.listdir("/proc"):
    if d.isdigit():
        try:
            if "airflow scheduler" in open(f"/proc/{d}/cmdline", "rb").read().replace(b"\\0", b" ").decode():
                pids.append(int(d))
        except OSError:
            pass
env = dict(kv.split("=", 1) for kv in open(f"/proc/{min(pids)}/environ", "rb").read().decode().split("\\0") if "=" in kv)
print(hashlib.sha256(env.get(var, "").strip().encode()).hexdigest())
"""


def docker_fingerprint(var: str) -> str:  # pragma: no cover - needs the running stack on the VM
    out = subprocess.run(
        ["docker", "exec", SCHEDULER, "python", "-c", _PROC_FINGERPRINT, var],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def vault_id_from(path: Path) -> str:
    vault_id = parse_env_file(path).get("BANK_HEALTH_VAULT_ID", "") if path.exists() else ""
    if not vault_id.startswith("ocid1.vault."):
        raise FetchError(f"BANK_HEALTH_VAULT_ID is missing from {path}")
    return vault_id


def oci_reader(vault_id: str) -> Reader:  # pragma: no cover - needs the VM's instance principal
    import oci  # only on the VM (deploy/requirements-vault.txt)

    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    client = oci.secrets.SecretsClient(config={}, signer=signer)

    def read(name: str) -> str:
        bundle = client.get_secret_bundle_by_name(secret_name=name, vault_id=vault_id).data
        return base64.b64decode(bundle.secret_bundle_content.content).decode("utf-8")

    return read


def main(
    argv: list[str] | None = None, reader: Reader | None = None, running: Fingerprint | None = None
) -> int:
    parser = argparse.ArgumentParser(description="Prod secrets from OCI Vault into RAM.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    fetch = sub.add_parser("fetch", help="Vault -> runtime dir (RAM)")
    fetch.add_argument("--runtime-dir", type=Path, default=RUNTIME_DIR)
    check = sub.add_parser("check-running", help="compare Vault with the running Airflow (match/differ only)")
    for p in (fetch, check):
        p.add_argument("--compose-env", type=Path, default=COMPOSE_ENV, help="holds BANK_HEALTH_VAULT_ID")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        read = reader or oci_reader(vault_id_from(args.compose_env))
        values = read_all(read)
    except FetchError as exc:
        print(f"VAULT FETCH FAILED: {exc}. Nothing was written.", file=sys.stderr)
        return 1
    if args.cmd == "fetch":
        if not args.runtime_dir.is_dir():
            print(
                f"VAULT FETCH FAILED: {args.runtime_dir} does not exist (the systemd unit creates it).",
                file=sys.stderr,
            )
            return 1
        written = write_runtime(values, args.runtime_dir)
        print(f"vault: {len(written)} secrets written to {args.runtime_dir} (RAM): " + ", ".join(written))
        skipped = [n for n in OPTIONAL_SECRETS if n not in values]
        if skipped:
            print("vault: optional, not in Vault (skipped): " + ", ".join(skipped))
        return 0
    results = compare_with_running(values, running or docker_fingerprint)
    for name, result in results:
        print(f"{name}: {result}")
    return 1 if any("DIFFERS" in r for _, r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
