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
    assert len(list(env.iterdir())) == 7
    if os.name == "posix":
        assert (env / "DATABRICKS_TOKEN").stat().st_mode & 0o777 == 0o600
    out = capsys.readouterr()
    assert "8 secrets written" in out.out
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


def test_check_env_reports_matches_and_differences_without_values(tmp_path, capsys):
    env_file = tmp_path / "old.env"
    env_file.write_text(
        "DATABRICKS_TOKEN=dapi-token\n"
        'SLACK_WEBHOOK_URL="value-of-prod-slack-webhook-123"\n'
        "DISCORD_WEBHOOK_URL=something-else\n"
        "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=\n"
    )
    assert vs.main(["check-env", str(env_file)], reader=fake_reader(VALUES)) == 1
    out = capsys.readouterr().out
    assert "prod-databricks-token: matches DATABRICKS_TOKEN" in out
    assert "prod-slack-webhook: matches SLACK_WEBHOOK_URL" in out
    assert "prod-discord-webhook: DIFFERS from DISCORD_WEBHOOK_URL" in out
    assert "prod-snowflake-key-passphrase: differs by design" in out
    assert "prod-airflow-fernet-key: AIRFLOW__CORE__FERNET_KEY not in the file" in out
    assert_no_values(out)
    assert "something-else" not in out


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
