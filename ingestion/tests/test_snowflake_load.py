from datetime import datetime, timezone

import pandas as pd
import pytest

from ingestion import snowflake_load as sl


class FakeDbx:
    """Stands in for DatabricksFiles: serves parquet bytes from memory."""

    def __init__(self, entries, files):
        self.entries, self.files = entries, files

    def list_directory(self, d):
        return self.entries

    def download_file(self, path, local):
        local.write_bytes(self.files[path])
        return local


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql):
        self.log.append(sql)

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.sql = []

    def cursor(self):
        return FakeCursor(self.sql)


def parquet_bytes(df, tmp_path, name):
    p = tmp_path / name
    df.to_parquet(p)
    return p.read_bytes()


def test_parquet_parts_skips_markers_and_dirs():
    dbx = FakeDbx(
        [
            {"path": "/Volumes/e/t/_SUCCESS", "is_directory": False},
            {"path": "/Volumes/e/t/_committed_123", "is_directory": False},
            {"path": "/Volumes/e/t/part-0001.snappy.parquet", "is_directory": False},
            {"path": "/Volumes/e/t/part-0000.snappy.parquet", "is_directory": False},
            {"path": "/Volumes/e/t/sub", "is_directory": True},
        ],
        {},
    )
    assert sl.parquet_parts(dbx, "/Volumes/e/t") == [
        "/Volumes/e/t/part-0000.snappy.parquet",
        "/Volumes/e/t/part-0001.snappy.parquet",
    ]


def test_prepare_frame_uppercases_and_stamps():
    ts = datetime(2026, 10, 19, tzinfo=timezone.utc)
    out = sl.prepare_frame(pd.DataFrame({"cert": [1], "repdte": ["20260630"]}), ts)
    assert list(out.columns) == ["CERT", "REPDTE", "_LOADED_AT"]
    assert out["_LOADED_AT"].iloc[0] == pd.Timestamp(ts)


def test_load_table_writes_parts_then_swaps(tmp_path):
    a = parquet_bytes(pd.DataFrame({"cert": [1, 2]}), tmp_path, "a.parquet")
    b = parquet_bytes(pd.DataFrame({"cert": [3]}), tmp_path, "b.parquet")
    dbx = FakeDbx(
        [{"path": "/Volumes/e/t/part-0.parquet"}, {"path": "/Volumes/e/t/part-1.parquet"}],
        {"/Volumes/e/t/part-0.parquet": a, "/Volumes/e/t/part-1.parquet": b},
    )
    writes = []

    def writer(conn, df, table, **kw):
        writes.append((table, len(df), kw["overwrite"], kw["schema"]))
        return True, 1, len(df), None

    conn = FakeConn()
    assert sl.load_table(conn, dbx, "FDIC_FINANCIALS", "/Volumes/e/t", writer=writer) == 3
    assert writes == [
        ("FDIC_FINANCIALS__LOADING", 2, True, "RAW"),
        ("FDIC_FINANCIALS__LOADING", 1, False, "RAW"),
    ]
    assert conn.sql == [
        "CREATE TABLE IF NOT EXISTS RAW.FDIC_FINANCIALS LIKE RAW.FDIC_FINANCIALS__LOADING",
        "ALTER TABLE RAW.FDIC_FINANCIALS SWAP WITH RAW.FDIC_FINANCIALS__LOADING",
        "DROP TABLE IF EXISTS RAW.FDIC_FINANCIALS__LOADING",
    ]


def test_load_table_fails_when_export_empty():
    with pytest.raises(FileNotFoundError):
        sl.load_table(FakeConn(), FakeDbx([], {}), "X", "/Volumes/e/t", writer=lambda *a, **k: None)


def test_load_all_skips_missing_optional_export(monkeypatch):
    calls = []

    def fake_load_table(conn, dbx, table, export_dir):
        calls.append(table)
        if table == "ML_RISK_SCORES":
            raise FileNotFoundError(export_dir)
        return 1

    monkeypatch.setattr(sl, "load_table", fake_load_table)
    out = sl.load_all(FakeConn(), FakeDbx([], {}), {"FDIC_FINANCIALS": "a", "ML_RISK_SCORES": "b"})
    assert out == {"FDIC_FINANCIALS": 1}
    assert calls == ["FDIC_FINANCIALS", "ML_RISK_SCORES"]


def test_load_all_fails_on_missing_required_export(monkeypatch):
    def fake_load_table(conn, dbx, table, export_dir):
        raise FileNotFoundError(export_dir)

    monkeypatch.setattr(sl, "load_table", fake_load_table)
    with pytest.raises(FileNotFoundError):
        sl.load_all(FakeConn(), FakeDbx([], {}), {"FDIC_FINANCIALS": "a"})


def test_resolve_key_path_falls_back_to_project_keys(tmp_path, monkeypatch):
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / "k.p8").write_text("x")
    monkeypatch.setattr(sl, "PROJECT_ROOT", tmp_path)
    assert sl.resolve_key_path("/opt/airflow/keys/k.p8") == str(tmp_path / "keys" / "k.p8")
    assert sl.resolve_key_path("/nowhere/other.p8") == "/nowhere/other.p8"


def test_failed_write_does_not_swap(tmp_path):
    a = parquet_bytes(pd.DataFrame({"cert": [1]}), tmp_path, "a.parquet")
    dbx = FakeDbx([{"path": "/Volumes/e/t/p.parquet"}], {"/Volumes/e/t/p.parquet": a})
    conn = FakeConn()
    with pytest.raises(RuntimeError):
        sl.load_table(conn, dbx, "X", "/Volumes/e/t", writer=lambda *a, **k: (False, 0, 0, None))
    assert conn.sql == []
