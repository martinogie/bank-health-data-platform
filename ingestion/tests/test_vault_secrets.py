"""deploy/vault_secrets.py and airflow/include/load_secrets.sh (Phase 4, PR 8).

Offline: a fake reader stands in for OCI Vault, tmp_path for /run/bank-health. Each test also
checks that no secret value is ever printed.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vault_secrets", ROOT / "deploy" / "vault_secrets.py")
vs = importlib.util.module_from_spec(spec)
sys.modules["vault_secrets"] = vs
spec.loader.exec_module(vs)

VALUES = {name: f"value-of-{name}-123" for name in vs.SECRETS}
VALUES["prod-databricks-token"] = "dapi-token\n"  # a pasted newline must be dropped
# A multi-line stand-in for the key file (deliberately not PEM-shaped: the secrets scan flags those).
VALUES["prod-snowflake-private-key"] = "fake key line 1\nfake key line 2"


def fake_reader(values):
    def read(name):
        if name not in values:
            raise KeyError("not found")
        return values[name]

    return read


def assert_no_values(text):
    for value in VALUES.values():
        assert value.strip() not in text


def test_fetch_writes_every_secret_into_the_runtime_dir(tmp_path, capsys):
    assert vs.main(["fetch", "--runtime-dir", str(tmp_path)], reader=fake_reader(VALUES)) == 0
    env = tmp_path / "env"
    assert (env / "DATABRICKS_TOKEN").read_text() == "dapi-token"
    assert (env / "SLACK_WEBHOOK_URL").read_text() == "value-of-prod-slack-webhook-123"
    key = (tmp_path / "keys" / "snowflake_rsa_key.p8").read_text()
    assert key == "fake key line 1\nfake key line 2\n"  # kept as is, one newline added at the end
    assert len(list(env.iterdir())) == 8
    if os.name == "posix":
        assert (env / "DATABRICKS_TOKEN").stat().st_mode & 0o777 == 0o600
    out = capsys.readouterr()
    assert "9 secrets written" in out.out
    assert_no_values(out.out + out.err)


def test_one_unreadable_secret_writes_nothing(tmp_path, capsys):
    values = dict(VALUES)
    del values["prod-discord-webhook"]
    values["prod-slack-webhook"] = "   "
    assert vs.main(["fetch", "--runtime-dir", str(tmp_path)], reader=fake_reader(values)) == 1
    assert list(tmp_path.iterdir()) == []
    err = capsys.readouterr().err
    assert "prod-discord-webhook (KeyError)" in err and "prod-slack-webhook (empty)" in err
    assert "Nothing was written" in err
    assert_no_values(err)


def test_a_failed_refresh_keeps_the_previous_files(tmp_path):
    assert vs.main(["fetch", "--runtime-dir", str(tmp_path)], reader=fake_reader(VALUES)) == 0
    broken = {k: v for k, v in VALUES.items() if k != "prod-airflow-jwt-secret"}
    assert vs.main(["fetch", "--runtime-dir", str(tmp_path)], reader=fake_reader(broken)) == 1
    assert (
        tmp_path / "env" / "AIRFLOW__API_AUTH__JWT_SECRET"
    ).read_text() == "value-of-prod-airflow-jwt-secret-123"


def test_fetch_refuses_a_missing_runtime_dir(tmp_path, capsys):
    missing = tmp_path / "nope"
    assert vs.main(["fetch", "--runtime-dir", str(missing)], reader=fake_reader(VALUES)) == 1
    assert "does not exist" in capsys.readouterr().err and not missing.exists()


def test_check_running_compares_fingerprints_without_values(capsys):
    import hashlib

    def running(var):  # what the running scheduler holds: all as in Vault, except Discord
        target = {t: n for n, (k, t) in vs.SECRETS.items() if k == "env"}[var]
        value = "something-else" if var == "DISCORD_WEBHOOK_URL" else VALUES[target]
        return hashlib.sha256(value.strip().encode()).hexdigest()

    assert vs.main(["check-running"], reader=fake_reader(VALUES), running=running) == 1
    out = capsys.readouterr().out
    assert "prod-databricks-token: matches DATABRICKS_TOKEN" in out  # stray newline ignored
    assert "prod-discord-webhook: DIFFERS from DISCORD_WEBHOOK_URL" in out
    assert "prod-snowflake-private-key: keys/snowflake_rsa_key.p8: a file, not compared" in out
    assert_no_values(out)
    assert "something-else" not in out and hashlib.sha256(b"x").hexdigest()[:8] not in out


def test_the_vault_id_comes_from_the_compose_env(tmp_path):
    compose_env = tmp_path / ".env"
    compose_env.write_text("BANK_HEALTH_ENV_FILE=/x\nBANK_HEALTH_VAULT_ID=ocid1.vault.oc1.test.abc\n")
    assert vs.vault_id_from(compose_env) == "ocid1.vault.oc1.test.abc"
    compose_env.write_text("BANK_HEALTH_ENV_FILE=/x\n")
    with pytest.raises(vs.FetchError, match="BANK_HEALTH_VAULT_ID is missing"):
        vs.vault_id_from(compose_env)


def test_load_secrets_sh_lists_the_same_names_as_the_fetch_script():
    text = (ROOT / "airflow" / "include" / "load_secrets.sh").read_text()
    line = next(l for l in text.splitlines() if l.startswith("BANK_HEALTH_VAULT_ENV_NAMES="))
    shell_names = set(line.split("=", 1)[1].strip('"').split())
    assert shell_names == {target for kind, target in vs.SECRETS.values() if kind == "env"}


BASH = shutil.which("bash")


def run_loader(secrets_dir, extra=""):
    script = (ROOT / "airflow" / "include" / "load_secrets.sh").as_posix()
    cmd = f'BANK_HEALTH_SECRETS_DIR="{secrets_dir}"; . "{script}"; {extra}'
    return subprocess.run([BASH, "-c", cmd], capture_output=True, text=True)


@pytest.mark.skipif(BASH is None or os.name != "posix", reason="needs a POSIX bash")
def test_load_secrets_sh_exports_in_vault_mode_and_stops_when_one_is_missing(tmp_path):
    assert vs.main(["fetch", "--runtime-dir", str(tmp_path)], reader=fake_reader(VALUES)) == 0
    ok = run_loader(tmp_path, 'printf "%s" "$DATABRICKS_TOKEN"')
    assert ok.returncode == 0 and ok.stdout == "dapi-token"
    (tmp_path / "env" / "DISCORD_WEBHOOK_URL").unlink()
    bad = run_loader(tmp_path, "echo started")
    assert bad.returncode == 1 and "missing" in bad.stderr and "DISCORD_WEBHOOK_URL" in bad.stderr
    assert "started" not in bad.stdout
    off = run_loader("", 'echo "env mode ${DATABRICKS_TOKEN:-unset}"')
    assert off.returncode == 0 and off.stdout.strip() == "env mode unset"


@pytest.mark.skipif(BASH is None or os.name != "posix", reason="needs a POSIX bash")
def test_cleanup_script_dry_run_then_apply(tmp_path):
    """deploy/vault_cleanup.sh against a fake /srv/bank-health (no Docker: --no-restart)."""
    root, runtime = tmp_path / "srv", tmp_path / "run"
    runtime.mkdir()  # on the VM the systemd unit creates it
    (root / "secrets" / "keys").mkdir(parents=True)
    (root / "app" / "airflow").mkdir(parents=True)
    (root / "app" / "airflow" / ".env").write_text(
        "BANK_HEALTH_RUNTIME_SECRETS_DIR=/run/bank-health\nBANK_HEALTH_KEYS_DIR=/run/bank-health/keys\n"
    )
    names = [t for k, t in vs.SECRETS.values() if k == "env"]
    lines = [f"{n}=secret-{i}" for i, n in enumerate(names)] + [
        "POSTGRES_PASSWORD=mirror",
        "SNOWFLAKE_ACCOUNT=keep-me",
    ]
    env = root / "secrets" / ".env"
    env.write_text("\n".join(lines) + "\n")
    for f in ("secrets/.env.save", "secrets/keys/snowflake_rsa_key.p8", "app/airflow/.env.before-vault"):
        (root / f).write_text("old")
    assert vs.main(["fetch", "--runtime-dir", str(runtime)], reader=fake_reader(VALUES)) == 0
    script = (ROOT / "deploy" / "vault_cleanup.sh").as_posix()
    run = lambda *a: subprocess.run(  # noqa: E731
        [BASH, script, *a], capture_output=True, text=True,
        env={**os.environ, "BANK_HEALTH_ROOT": str(root), "BANK_HEALTH_RUNTIME": str(runtime)},
    )  # fmt: skip

    dry = run("--no-restart")
    assert dry.returncode == 0 and "DRY RUN" in dry.stdout and "9 line(s)" in dry.stdout
    assert env.read_text().count("=") == len(lines)  # nothing changed

    done = run("--apply", "--no-restart")
    assert done.returncode == 0, done.stderr
    assert env.read_text() == "SNOWFLAKE_ACCOUNT=keep-me\n"
    assert (
        not (root / "secrets" / ".env.save").exists()
        and not (root / "secrets/keys/snowflake_rsa_key.p8").exists()
    )
    assert not (root / "app/airflow/.env.before-vault").exists()
    assert "secret-" not in done.stdout + done.stderr  # values never printed


@pytest.mark.skipif(BASH is None or os.name != "posix", reason="needs a POSIX bash")
def test_cleanup_script_refuses_without_vault_mode(tmp_path):
    root = tmp_path / "srv"
    (root / "app" / "airflow").mkdir(parents=True)
    (root / "app" / "airflow" / ".env").write_text("BANK_HEALTH_KEYS_DIR=/srv/bank-health/secrets/keys\n")
    r = subprocess.run(
        [BASH, (ROOT / "deploy" / "vault_cleanup.sh").as_posix(), "--apply", "--no-restart"],
        capture_output=True, text=True, env={**os.environ, "BANK_HEALTH_ROOT": str(root)},
    )  # fmt: skip
    assert r.returncode == 1 and "Vault mode is not on" in r.stderr


def test_cleanup_script_lists_the_same_vault_names_as_the_fetch_script():
    text = (ROOT / "deploy" / "vault_cleanup.sh").read_text()
    block = text[text.index("local vault_names=(") : text.index(")", text.index("local vault_names=("))]
    names = set(block.replace("local vault_names=(", "").split())
    assert names == {t for k, t in vs.SECRETS.values() if k == "env"}


def run_db_conn(env_extra):
    script = (ROOT / "airflow" / "include" / "db_conn.sh").as_posix()
    env = {k: v for k, v in os.environ.items() if not k.startswith("BANK_HEALTH_")}
    return subprocess.run([BASH, script], capture_output=True, text=True, env={**env, **env_extra})


@pytest.mark.skipif(BASH is None or os.name != "posix", reason="needs a POSIX bash")
def test_db_conn_uses_the_default_password_until_the_switch(tmp_path):
    r = run_db_conn({})
    assert r.returncode == 0 and r.stdout == "postgresql+psycopg2://airflow:airflow@postgres/airflow"


@pytest.mark.skipif(BASH is None or os.name != "posix", reason="needs a POSIX bash")
def test_db_conn_reads_the_vault_password_after_the_switch(tmp_path):
    (tmp_path / "env").mkdir()
    (tmp_path / "env" / "AIRFLOW_DB_PASSWORD").write_text("abc123XYZabc123XYZabc123")
    on = {"BANK_HEALTH_DB_PASSWORD_FROM_VAULT": "1", "BANK_HEALTH_SECRETS_DIR": str(tmp_path)}
    r = run_db_conn(on)
    assert (
        r.returncode == 0
        and r.stdout == "postgresql+psycopg2://airflow:abc123XYZabc123XYZabc123@postgres/airflow"
    )
    (tmp_path / "env" / "AIRFLOW_DB_PASSWORD").write_text("has spaces & @ signs")
    bad = run_db_conn(on)
    assert bad.returncode == 1 and "letters and digits only" in bad.stderr and "@ signs" not in bad.stderr
    (tmp_path / "env" / "AIRFLOW_DB_PASSWORD").unlink()
    missing = run_db_conn(on)
    assert missing.returncode == 1 and "is missing" in missing.stderr


def test_optional_r2_secrets_are_written_when_present_and_skipped_when_not(tmp_path, capsys):
    # Not in Vault yet: the fetch still succeeds (Airflow must start), and says what it skipped.
    assert vs.main(["fetch", "--runtime-dir", str(tmp_path)], reader=fake_reader(VALUES)) == 0
    assert not (tmp_path / "env" / "R2_ENDPOINT").exists()
    out = capsys.readouterr().out
    assert (
        "optional, not in Vault (skipped): prod-r2-access-key-id, prod-r2-secret-access-key, prod-r2-endpoint"
        in out
    )
    # In Vault: written like the others, never printed.
    r2 = {"prod-r2-access-key-id": "r2-key-id-xyz", "prod-r2-secret-access-key": "r2-secret-xyz",
          "prod-r2-endpoint": "https://acct.r2.example"}  # fmt: skip
    assert vs.main(["fetch", "--runtime-dir", str(tmp_path)], reader=fake_reader({**VALUES, **r2})) == 0
    assert (tmp_path / "env" / "R2_SECRET_ACCESS_KEY").read_text() == "r2-secret-xyz"
    out = capsys.readouterr()
    assert "12 secrets written" in out.out and "skipped" not in out.out
    for value in r2.values():
        assert value not in out.out + out.err


def test_load_secrets_sh_lists_the_same_optional_names():
    text = (ROOT / "airflow" / "include" / "load_secrets.sh").read_text()
    line = next(l for l in text.splitlines() if l.startswith("BANK_HEALTH_VAULT_OPTIONAL_ENV_NAMES="))
    shell_names = set(line.split("=", 1)[1].strip('"').split())
    assert shell_names == {target for kind, target in vs.OPTIONAL_SECRETS.values() if kind == "env"}
