"""Integration: Databricks Files API against the real bank_health raw_files Volume.

Uploads a small probe file under _integration_test/, lists it, downloads it,
compares bytes, then deletes it. Needs DATABRICKS_HOST and DATABRICKS_TOKEN in .env.
"""

from __future__ import annotations

import uuid

import pytest

from ingestion.databricks_files import RAW_VOLUME, DatabricksFiles
from tests.integration.conftest import require_env

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def dbx():
    require_env("DATABRICKS_HOST", "DATABRICKS_TOKEN")
    return DatabricksFiles.from_env()


def test_upload_list_download_delete_roundtrip(dbx, tmp_path):
    payload = f"probe {uuid.uuid4()}".encode()
    local_root = tmp_path / "raw"
    local = local_root / "_integration_test" / "probe.txt"
    local.parent.mkdir(parents=True)
    local.write_bytes(payload)

    [remote] = dbx.upload_files([local], local_root)
    assert remote == f"{RAW_VOLUME}/_integration_test/probe.txt"
    try:
        listed = {e["path"] for e in dbx.list_directory(f"{RAW_VOLUME}/_integration_test")}
        assert remote in listed
        back = dbx.download_file(remote, tmp_path / "back.txt")
        assert back.read_bytes() == payload
    finally:
        dbx.delete_file(remote)

    listed_after = {e["path"] for e in dbx.list_directory(f"{RAW_VOLUME}/_integration_test")}
    assert remote not in listed_after


def test_expected_raw_folders_exist(dbx):
    """00_setup created the layout that uploads mirror."""
    top = {e["name"].rstrip("/") for e in dbx.list_directory(RAW_VOLUME) if e.get("is_directory")}
    assert {"fdic", "cfpb"} <= top
