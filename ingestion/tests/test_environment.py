"""Environment safety, code side: BANK_HEALTH_ENV is required, and only prod writes prod objects,
publishes, or moves @champion. Every code path that writes is checked with fakes, offline.

The grant side (DEV_ROLE and CI_ROLE cannot write BANK_HEALTH even if the code tried) is
tests/integration/test_environment_safety.py.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from ingestion import databricks_jobs as dj
from ingestion import dbt_manifest, environment, snowflake_load, watchlist_export
from ingestion.databricks_files import DatabricksFiles
from ingestion.environment import EnvironmentMismatch, EnvironmentNotSet, ProdWriteRefused
from ingestion.tests.conftest import FakeResponse, FakeSession

HOST = "https://dbc-example.cloud.databricks.com"
NOTEBOOKS = Path(__file__).resolve().parents[2] / "databricks" / "notebooks"


# ------------------------------------------------------------ BANK_HEALTH_ENV is required
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_environment_stops_with_a_clear_error(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("BANK_HEALTH_ENV", raising=False)
    else:
        monkeypatch.setenv("BANK_HEALTH_ENV", value)
    with pytest.raises(EnvironmentNotSet, match="BANK_HEALTH_ENV is not set.*no default"):
        environment.current_env()


@pytest.mark.parametrize("value", ["Prod", "PROD", "production", "staging", "dev,prod", "test"])
def test_unknown_environment_is_refused(monkeypatch, value):
    monkeypatch.setenv("BANK_HEALTH_ENV", value)
    with pytest.raises(EnvironmentNotSet, match="not valid"):
        environment.settings()


@pytest.mark.parametrize(
    ("env", "database", "catalog", "job", "target"),
    [
        ("prod", "BANK_HEALTH", "bank_health", "bank_health_pipeline", "prod"),
        ("dev", "BANK_HEALTH_DEV", "bank_health_dev", "bank_health_pipeline_dev", "dev"),
        ("ci", "BANK_HEALTH_DEV", None, None, "ci"),
    ],
)
def test_each_environment_has_its_own_objects(monkeypatch, env, database, catalog, job, target):
    monkeypatch.setenv("BANK_HEALTH_ENV", env)
    s = environment.settings()
    assert (s.snowflake_database, s.databricks_catalog, s.databricks_job_name, s.dbt_target) == (
        database,
        catalog,
        job,
        target,
    )
    assert s.is_prod == (env == "prod")


def test_dev_env_with_prod_database_in_env_file_is_refused(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "BANK_HEALTH")
    with pytest.raises(EnvironmentMismatch, match="expects SNOWFLAKE_DATABASE=BANK_HEALTH_DEV"):
        environment.settings()


def test_prod_env_with_dev_database_in_env_file_is_refused(monkeypatch):
    monkeypatch.setenv("BANK_HEALTH_ENV", "prod")
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "bank_health_dev")
    with pytest.raises(EnvironmentMismatch):
        environment.settings()


def test_catalog_override_must_match_the_environment(monkeypatch):
    monkeypatch.setenv("DATABRICKS_CATALOG", "bank_health")
    with pytest.raises(EnvironmentMismatch, match="Remove DATABRICKS_CATALOG"):
        environment.settings()
    monkeypatch.setenv("DATABRICKS_CATALOG", "bank_health_dev")
    assert environment.databricks_catalog() == "bank_health_dev"


def test_describe_names_objects_only():
    line = environment.describe()
    assert line.startswith("environment=dev snowflake_database=BANK_HEALTH_DEV")
    assert "token" not in line.lower() and "key" not in line.lower()


# ------------------------------------------------------------ what counts as prod
@pytest.mark.parametrize(
    ("name", "is_prod"),
    [
        ("BANK_HEALTH", True),
        ("bank_health", True),
        ("BANK_HEALTH.RAW.FDIC_FINANCIALS", True),
        ('"BANK_HEALTH".MARTS', True),
        ("bank_health.ml.distress_model", True),
        ("/Volumes/bank_health/bronze/raw_files/x.csv", True),
        ("BANK_HEALTH_DEV", False),
        ("BANK_HEALTH_DEV.RAW.FDIC_FINANCIALS", False),
        ("bank_health_dev.ml.distress_model", False),
        ("/Volumes/bank_health_dev/bronze/raw_files/x.csv", False),
    ],
)
def test_is_prod_object(name, is_prod):
    assert environment.is_prod_object(name) is is_prod


@pytest.mark.parametrize("env", ["dev", "ci"])
def test_non_prod_cannot_write_publish_or_promote(monkeypatch, env):
    monkeypatch.setenv("BANK_HEALTH_ENV", env)
    with pytest.raises(ProdWriteRefused):
        environment.assert_can_write("BANK_HEALTH.RAW.FDIC_FINANCIALS")
    with pytest.raises(ProdWriteRefused):
        environment.assert_can_publish("the watchlist")
    with pytest.raises(ProdWriteRefused):
        environment.assert_can_promote("bank_health.ml.distress_model")
    environment.assert_can_write("BANK_HEALTH_DEV.RAW.FDIC_FINANCIALS")  # its own objects are fine


def test_prod_may_write_publish_and_promote(monkeypatch):
    monkeypatch.setenv("BANK_HEALTH_ENV", "prod")
    environment.assert_can_write("BANK_HEALTH.RAW.FDIC_FINANCIALS")
    environment.assert_can_publish("the watchlist")
    environment.assert_can_promote("bank_health.ml.distress_model")


# ------------------------------------------------------------ Snowflake loader
class RecordingCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, *args):
        self.conn.sql.append(sql)

    def fetchone(self):
        return (self.conn.database,)

    def fetchall(self):
        return self.conn.listing

    def close(self):
        pass


class RecordingConn:
    """Records SQL; its session is in `database`."""

    def __init__(self, database, listing=()):
        self.database, self.listing, self.sql = database, list(listing), []

    def cursor(self):
        return RecordingCursor(self)


class NoDbx:
    def __getattr__(self, name):
        raise AssertionError(f"Databricks must not be touched (called {name})")


def test_dev_loader_connected_to_prod_writes_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("load_table must not run")

    monkeypatch.setattr(snowflake_load, "load_table", boom)
    conn = RecordingConn("BANK_HEALTH")
    with pytest.raises(EnvironmentMismatch, match="expected BANK_HEALTH_DEV"):
        snowflake_load.load_all(conn, NoDbx())
    assert conn.sql == ["select current_database()"]  # the check only; no write was sent


def test_dev_loader_reads_dev_exports(monkeypatch):
    seen = []
    monkeypatch.setattr(snowflake_load, "load_table", lambda conn, dbx, table, d: seen.append(d) or 1)
    snowflake_load.load_all(RecordingConn("BANK_HEALTH_DEV"), NoDbx(), {"FDIC_FINANCIALS": "fdic_financials"})
    assert seen == ["/Volumes/bank_health_dev/gold/exports/fdic_financials"]


def test_prod_loader_needs_the_prod_database(monkeypatch):
    monkeypatch.setenv("BANK_HEALTH_ENV", "prod")
    assert snowflake_load.check_target_database(RecordingConn("BANK_HEALTH")) == "BANK_HEALTH"
    with pytest.raises(EnvironmentMismatch):
        snowflake_load.check_target_database(RecordingConn("BANK_HEALTH_DEV"))


# ------------------------------------------------------------ Databricks Files API
def _files():
    session = FakeSession(lambda *a: FakeResponse(payload={}))
    return DatabricksFiles(HOST, "dapi-test", session=session), session


def test_dev_cannot_upload_to_or_delete_from_the_prod_volume(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("x")
    dbx, session = _files()
    with pytest.raises(ProdWriteRefused):
        dbx.upload_file(f, "/Volumes/bank_health/bronze/raw_files/a.csv")
    with pytest.raises(ProdWriteRefused):
        dbx.delete_file("/Volumes/bank_health/bronze/raw_files/a.csv")
    assert session.calls == []  # refused before any HTTP request


def test_dev_uploads_go_to_the_dev_volume(tmp_path):
    (tmp_path / "fdic").mkdir()
    f = tmp_path / "fdic" / "x.jsonl.gz"
    f.write_text("x")
    dbx, _ = _files()
    assert dbx.upload_files([f], tmp_path) == ["/Volumes/bank_health_dev/bronze/raw_files/fdic/x.jsonl.gz"]


# ------------------------------------------------------------ watchlist and dbt manifest
def test_dev_cannot_publish_the_watchlist(tmp_path):
    conn = RecordingConn("BANK_HEALTH_DEV")
    out = tmp_path / "watchlist.json"
    with pytest.raises(ProdWriteRefused, match="watchlist"):
        watchlist_export.export(conn, out)
    assert conn.sql == [] and not out.exists()  # refused before querying or writing


def _manifest(tmp_path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"metadata": {"dbt_version": "1.12.6"}, "nodes": {"a": {}, "b": {}}}))
    return path


@pytest.mark.parametrize("env", ["dev", "ci"])
def test_only_prod_publishes_the_dbt_manifest(monkeypatch, tmp_path, env):
    monkeypatch.setenv("BANK_HEALTH_ENV", env)
    conn = RecordingConn("BANK_HEALTH_DEV")
    with pytest.raises(ProdWriteRefused):
        dbt_manifest.publish_manifest(conn, _manifest(tmp_path))
    assert conn.sql == []


def test_prod_publishes_the_manifest_to_the_stage(monkeypatch, tmp_path):
    monkeypatch.setenv("BANK_HEALTH_ENV", "prod")
    conn = RecordingConn("BANK_HEALTH")
    out = dbt_manifest.publish_manifest(conn, _manifest(tmp_path))
    assert out["nodes"] == 2 and out["stage"] == "BANK_HEALTH.RAW.DBT_ARTIFACTS"
    put = conn.sql[-1]
    assert put.startswith("PUT 'file://") and "@BANK_HEALTH.RAW.DBT_ARTIFACTS" in put
    assert "OVERWRITE=TRUE" in put and "AUTO_COMPRESS=FALSE" in put


def test_fetch_manifest_reports_an_empty_stage(tmp_path):
    conn = RecordingConn("BANK_HEALTH_DEV", listing=[])
    assert dbt_manifest.fetch_manifest(conn, tmp_path / "prod-manifest") is None


def test_check_manifest_rejects_other_json(tmp_path):
    bad = tmp_path / "manifest.json"
    bad.write_text("{}")
    with pytest.raises(ValueError, match="not a dbt manifest"):
        dbt_manifest.check_manifest(bad)


# ------------------------------------------------------------ Databricks job and notebooks
def _api(handler):
    session = FakeSession(handler)
    return dj.DatabricksAPI(HOST, "dapi-test", session=session), session


def _workspace_handler(repos):
    def handler(method, url, kw):
        if url.endswith("/scim/v2/Me"):
            return FakeResponse(payload={"userName": "me@example.com"})
        if url.endswith("/api/2.0/repos") and method == "GET":
            return FakeResponse(payload={"repos": repos})
        if "/api/2.0/repos/" in url:
            rid = int(url.rsplit("/", 1)[1])
            return FakeResponse(payload=next(r for r in repos if r["id"] == rid))
        return FakeResponse(payload={})

    return handler


SAME_URL = "https://github.com/martinogie/bank-health-data-platform"


def test_prod_picks_its_git_folder_by_path_even_if_another_has_the_same_url(monkeypatch):
    monkeypatch.setenv("BANK_HEALTH_ENV", "prod")
    repos = [
        {"id": 1, "url": SAME_URL, "path": "/Users/me@example.com/some-other-copy"},
        {"id": 2, "url": SAME_URL, "path": "/Users/me@example.com/bank-health-data-platform"},
    ]
    api, session = _api(_workspace_handler(repos))
    assert dj.sync_notebooks(api) == "/Users/me@example.com/bank-health-data-platform"
    patches = [(url, kw["json"]) for m, url, kw in session.calls if m == "PATCH"]
    assert patches == [(f"{HOST}/api/2.0/repos/2", {"branch": "main"})]


def test_dev_uploads_notebooks_and_never_touches_a_git_folder():
    api, session = _api(_workspace_handler([]))
    root = dj.sync_notebooks(api)
    assert root == "/Users/me@example.com/bank-health-dev-run"
    assert not any(m == "PATCH" or "/repos" in url for m, url, _ in session.calls)
    imports = [kw["json"]["path"] for m, url, kw in session.calls if url.endswith("/workspace/import")]
    assert f"{root}/databricks/notebooks/05_score" in imports
    # sub-folders keep their layout (dev test notebooks in tools/ use %run ../_common)
    assert f"{root}/databricks/notebooks/tools/test_silver_merge" in imports
    folders = [kw["json"]["path"] for m, url, kw in session.calls if url.endswith("/workspace/mkdirs")]
    assert f"{root}/databricks/notebooks/tools" in folders
    assert not any("__pycache__" in p for p in imports)


def test_upload_never_overwrites_the_prod_git_folder():
    api, session = _api(_workspace_handler([]))
    with pytest.raises(ProdWriteRefused):
        dj.upload_notebooks(api, "bank-health-data-platform")
    assert session.calls == []


def test_ci_has_no_databricks_job(monkeypatch):
    monkeypatch.setenv("BANK_HEALTH_ENV", "ci")
    with pytest.raises(EnvironmentMismatch):
        dj.job_settings("/Users/me/x")


@pytest.mark.parametrize(
    ("env", "job", "catalog"),
    [("dev", "bank_health_pipeline_dev", "bank_health_dev"), ("prod", "bank_health_pipeline", "bank_health")],
)
def test_job_passes_its_environment_to_the_notebooks(monkeypatch, env, job, catalog):
    monkeypatch.setenv("BANK_HEALTH_ENV", env)
    s = dj.job_settings("/Users/me/x")
    assert s["name"] == job
    assert {"name": "env", "default": env} in s["parameters"]
    assert {"name": "catalog", "default": catalog} in s["parameters"]


class FakeWidgets:
    def __init__(self, values):
        self.values = values

    def text(self, name, default, label=None):
        self.values.setdefault(name, default)

    def get(self, name):
        return self.values[name]


def run_common(widgets: dict) -> dict:
    """Execute databricks/notebooks/_common.py with fake widgets and stub pyspark modules."""
    pyspark = types.ModuleType("pyspark")
    sql = types.ModuleType("pyspark.sql")
    sql.Window = object
    sql.functions = types.ModuleType("pyspark.sql.functions")
    stubs = {"pyspark": pyspark, "pyspark.sql": sql, "pyspark.sql.functions": sql.functions}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        ns = {"dbutils": types.SimpleNamespace(widgets=FakeWidgets(dict(widgets)))}
        path = NOTEBOOKS / "_common.py"
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
        return ns
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_notebooks_stop_without_env():
    with pytest.raises(ValueError, match="must be dev or prod"):
        run_common({"catalog": "bank_health"})


def test_notebooks_stop_when_env_and_catalog_disagree():
    with pytest.raises(ValueError, match="uses catalog bank_health_dev"):
        run_common({"env": "dev", "catalog": "bank_health"})


def test_dev_notebooks_read_prod_champion_but_never_promote():
    ns = run_common({"env": "dev", "catalog": "bank_health_dev"})
    assert ns["MODEL_NAME"] == "bank_health_dev.ml.distress_model"  # dev registers here
    assert ns["CHAMPION_MODEL"] == "bank_health.ml.distress_model"  # read-only reference
    assert ns["EXPERIMENT_NAME"].endswith("_dev")
    ns["check_promote"](False)
    with pytest.raises(PermissionError, match="only prod may move"):
        ns["check_promote"](True)


def test_prod_notebooks_may_promote():
    ns = run_common({"env": "prod", "catalog": "bank_health"})
    assert ns["MODEL_NAME"] == ns["CHAMPION_MODEL"] == "bank_health.ml.distress_model"
    ns["check_promote"](True)
