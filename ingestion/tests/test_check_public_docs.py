"""scripts/check_public_docs.py: it must find planted identifiers, and pass clean text."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_public_docs as cpd  # noqa: E402


def test_clean_docs_pass():
    text = "<html>fct_bank_quarter: one row per bank per quarter. dbt=1.12.6 served on 127.0.0.1</html>"
    assert cpd.scan(text, {}) == []


def test_each_generic_pattern_is_caught():
    planted = {
        "email address": "contact someone.name@example.com today",
        "IPv4 address": "host 203.0.113.42 answered",
        "OCI identifier": "ocid1.instance.oc1.us-chicago-1.aaaa",
        "Snowflake hostname": "abc12345.snowflakecomputing.com",
        "Databricks hostname": "dbc-1234abcd-5678.cloud.databricks.com",
        "private key": "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----",
        "home path": r"C:\Users\someone\project",
        "AWS key": "AKIA" + "ABCDEFGHIJKLMNOP",  # built from pieces: the secrets scan flags the literal
    }
    for rule, text in planted.items():
        hits = cpd.scan(text, {})
        assert any(h.startswith(rule) for h in hits), rule


def test_real_values_are_found_case_insensitively_and_never_printed(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "ORGX-ACCT99")
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-zz.example")
    page = tmp_path / "index.html"
    page.write_text("profile orgx-acct99 and dbc-zz.example")
    assert cpd.main([str(page)]) == 1
    out = capsys.readouterr().out
    assert "value of SNOWFLAKE_ACCOUNT (1x)" in out and "value of DATABRICKS_HOST (1x)" in out
    assert "orgx-acct99" not in out.lower() and "dbc-zz" not in out


def test_placeholders_and_short_values_are_ignored(monkeypatch):
    for name in cpd.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "placeholder")
    monkeypatch.setenv("SNOWFLAKE_USER", "ab")
    assert cpd.env_values() == {}


def test_placeholders_ignore_the_r2_endpoint_when_unset(monkeypatch):
    for name in [*cpd.ENV_NAMES, "R2_ENDPOINT"]:
        monkeypatch.delenv(name, raising=False)
    assert cpd.env_values() == {}


def test_the_r2_endpoint_and_its_account_id_are_caught_and_never_shown():
    from ingestion.public_scan import env_values, scan, scan_files

    secrets = env_values({"R2_ENDPOINT": "https://0123456789abcdef.r2.cloudflarestorage.com"})
    assert set(secrets) == {"R2_ENDPOINT", "R2 account ID"}
    hits = scan("account 0123456789ABCDEF in a file", secrets)
    assert hits == ["value of R2 account ID (1x)"]
    assert any(h.startswith("R2 endpoint") for h in scan("x.r2.cloudflarestorage.com", {}))
    found = scan_files({"a.json": b'{"ok": 1}', "b.json": b"0123456789abcdef"}, secrets)
    assert list(found) == ["b.json"] and "0123456789abcdef" not in str(found)
