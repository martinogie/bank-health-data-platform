import pytest

from ingestion.databricks_files import RAW_VOLUME, DatabricksFiles
from ingestion.tests.conftest import FakeResponse, FakeSession

HOST = "https://dbc-example.cloud.databricks.com"


def client(handler):
    session = FakeSession(handler)
    return DatabricksFiles(HOST + "/", "dapi-test-token", session=session), session


def test_requires_host_and_token():
    with pytest.raises(ValueError):
        DatabricksFiles("", "")


def test_repr_hides_token():
    dbx, _ = client(lambda *a: FakeResponse())
    assert "dapi" not in repr(dbx)


def test_rejects_paths_outside_volumes():
    dbx, _ = client(lambda *a: FakeResponse())
    with pytest.raises(ValueError):
        dbx._url("files", "/Workspace/Users/x")


def test_upload_files_mirrors_local_layout(tmp_path):
    root = tmp_path / "raw"
    f = root / "fdic" / "financials" / "financials_20260630.jsonl.gz"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"hello")

    dbx, session = client(lambda *a: FakeResponse(204))
    targets = dbx.upload_files([f], root)

    assert targets == [f"{RAW_VOLUME}/fdic/financials/financials_20260630.jsonl.gz"]
    method, url, kw = session.calls[0]
    assert method == "PUT"
    assert url == f"{HOST}/api/2.0/fs/files{targets[0]}"
    assert kw["params"] == {"overwrite": "true"}
    assert kw["body_bytes"] == b"hello"
    assert session.headers["Authorization"] == "Bearer dapi-test-token"


def test_upload_retries_and_rewinds_body(tmp_path, no_sleep):
    f = tmp_path / "a.csv.gz"
    f.write_bytes(b"data")
    responses = iter([FakeResponse(503), FakeResponse(204)])
    dbx, session = client(lambda *a: next(responses))
    dbx.upload_file(f, f"{RAW_VOLUME}/a.csv.gz")
    assert [c[2]["body_bytes"] for c in session.calls] == [b"data", b"data"]


def test_client_error_is_raised_with_message(tmp_path):
    f = tmp_path / "a"
    f.write_bytes(b"x")
    dbx, _ = client(lambda *a: FakeResponse(403, content=b"PERMISSION_DENIED"))
    with pytest.raises(Exception, match="403 PERMISSION_DENIED"):
        dbx.upload_file(f, f"{RAW_VOLUME}/a")


def test_list_directory_follows_page_tokens():
    pages = {
        None: {"contents": [{"path": "/Volumes/x/a"}], "next_page_token": "t2"},
        "t2": {"contents": [{"path": "/Volumes/x/b"}]},
    }
    dbx, _ = client(lambda m, u, kw: FakeResponse(payload=pages[kw["params"].get("page_token")]))
    assert [e["path"] for e in dbx.list_directory("/Volumes/x/")] == ["/Volumes/x/a", "/Volumes/x/b"]


def test_delete_file():
    dbx, session = client(lambda *a: FakeResponse(204))
    dbx.delete_file("/Volumes/x/probe.txt")
    assert session.calls[0][:2] == ("DELETE", f"{HOST}/api/2.0/fs/files/Volumes/x/probe.txt")


def test_download_file(tmp_path):
    dbx, _ = client(lambda *a: FakeResponse(content=b"parquet-bytes"))
    out = dbx.download_file("/Volumes/x/part.parquet", tmp_path / "sub" / "p.parquet")
    assert out.read_bytes() == b"parquet-bytes"
