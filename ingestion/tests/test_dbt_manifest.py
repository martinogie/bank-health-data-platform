"""Unit tests for ingestion/dbt_manifest.py: fetching the prod manifest for dbt slim CI.

The Snowflake cursor is faked: GET "downloads" by writing the manifest into the target folder.
Publishing (prod only) is covered in test_environment.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion import dbt_manifest

MANIFEST = {"metadata": {"dbt_version": "1.12.6", "generated_at": "2026-10-26T11:40:00Z"}, "nodes": {"m": {}}}


class StageCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql):
        self.conn.sql.append(sql)
        if sql.startswith("GET "):
            folder = sql.split("'file://", 1)[1].rstrip("/'")
            (Path(folder) / "manifest.json").write_text(json.dumps(self.conn.content))

    def fetchall(self):
        return [("dbt_artifacts/manifest.json", 1234)] if self.conn.content is not None else []

    def close(self):
        self.conn.closed_cursors += 1


class StageConn:
    def __init__(self, content=MANIFEST):
        self.content, self.sql, self.closed_cursors, self.closed = content, [], 0, False

    def cursor(self):
        return StageCursor(self)

    def close(self):
        self.closed = True


def test_fetch_downloads_and_checks_the_manifest(tmp_path):
    conn = StageConn()
    path = dbt_manifest.fetch_manifest(conn, tmp_path / "prod-manifest")
    assert path == tmp_path / "prod-manifest" / "manifest.json"
    assert dbt_manifest.check_manifest(path)["nodes"] == 1
    assert conn.sql[0].startswith("LIST @BANK_HEALTH.RAW.DBT_ARTIFACTS")
    assert conn.sql[1].startswith("GET @BANK_HEALTH.RAW.DBT_ARTIFACTS/manifest.json 'file://")
    assert conn.closed_cursors == 1


def test_fetch_rejects_a_file_that_is_not_a_manifest(tmp_path):
    with pytest.raises(ValueError, match="not a dbt manifest"):
        dbt_manifest.fetch_manifest(StageConn(content={"hello": 1}), tmp_path)


def test_check_manifest_summary(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(MANIFEST))
    assert dbt_manifest.check_manifest(path) == {
        "nodes": 1,
        "dbt_version": "1.12.6",
        "generated_at": "2026-10-26T11:40:00Z",
    }


def test_publish_needs_a_file_named_manifest_json(monkeypatch, tmp_path):
    monkeypatch.setenv("BANK_HEALTH_ENV", "prod")
    other = tmp_path / "other.json"
    other.write_text(json.dumps(MANIFEST))

    class ProdConn(StageConn):
        def cursor(self):
            cur = StageCursor(self)
            cur.fetchone = lambda: ("BANK_HEALTH",)
            return cur

    with pytest.raises(ValueError, match="named manifest.json"):
        dbt_manifest.publish_manifest(ProdConn(), other)


@pytest.mark.parametrize(("content", "code"), [(MANIFEST, 0), (None, dbt_manifest.NOT_FOUND_EXIT_CODE)])
def test_command_line_exit_codes(monkeypatch, tmp_path, capsys, content, code):
    conn = StageConn(content=content)
    monkeypatch.setattr("ingestion.snowflake_load.connect_from_env", lambda: conn)
    assert dbt_manifest.main(["fetch", "--out", str(tmp_path)]) == code
    assert conn.closed
    out = capsys.readouterr()
    if code == 0:
        assert out.out.strip().endswith("manifest.json")
    else:
        assert "no manifest.json" in out.err


def _node(schema):
    return {"schema": schema, "relation_name": f"BANK_HEALTH.{schema}.FCT_BANK_QUARTER"}


def test_a_manifest_pointing_at_the_published_marts_is_accepted(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({**MANIFEST, "nodes": {"model.fct": _node("MARTS"), "model.stg": _node("STAGING")}})
    )
    assert dbt_manifest.check_manifest(path)["nodes"] == 2


@pytest.mark.parametrize(
    "node",
    [
        _node("MARTS_NEXT"),
        {"schema": "marts_next"},
        {"relation_name": '"BANK_HEALTH"."MARTS_NEXT"."FCT_COMPLAINTS"'},
    ],
)
def test_a_manifest_pointing_at_marts_next_is_refused(tmp_path, node):
    """After write-audit-publish, MARTS_NEXT holds last week's marts: slim CI must never defer to it."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({**MANIFEST, "nodes": {"model.fct": node}}))
    with pytest.raises(ValueError, match="MARTS_NEXT, not the published MARTS"):
        dbt_manifest.check_manifest(path)
