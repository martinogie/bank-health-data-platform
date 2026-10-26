import pytest

from ingestion import databricks_jobs as dj
from ingestion.tests.conftest import FakeResponse, FakeSession

HOST = "https://dbc-example.cloud.databricks.com"


def api_with(handler):
    session = FakeSession(handler)
    return dj.DatabricksAPI(HOST, "dapi-test", session=session), session


def test_job_settings_chain_and_safety_flags():
    s = dj.job_settings("/Users/me/bank-health-data-platform")
    keys = [t["task_key"] for t in s["tasks"]]
    assert keys == ["bronze", "silver", "gold", "train_model", "score"]
    for prev, task in zip(s["tasks"], s["tasks"][1:]):
        assert task["depends_on"] == [{"task_key": prev["task_key"]}]
    assert (
        s["tasks"][0]["notebook_task"]["notebook_path"]
        == "/Users/me/bank-health-data-platform/databricks/notebooks/01_bronze"
    )
    assert s["max_concurrent_runs"] == 1  # retries can't run in parallel
    assert {"name": "retrain", "default": "false"} in s["parameters"]
    assert all(t["max_retries"] == 0 for t in s["tasks"])
    assert "new_cluster" not in str(s) and "existing_cluster_id" not in str(s)  # serverless


def test_every_task_runs_in_the_pinned_environment():
    s = dj.job_settings("/Users/me/bank-health-data-platform")
    assert s["environments"] == [{"environment_key": "pinned", "spec": {"environment_version": "2"}}]
    assert all(t["environment_key"] == "pinned" for t in s["tasks"])


def test_create_job_when_missing():
    def handler(method, url, kw):
        if url.endswith("/jobs/list"):
            return FakeResponse(payload={})
        return FakeResponse(payload={"job_id": 42})

    api, session = api_with(handler)
    assert dj.create_or_update_job(api, {"name": "j"}) == 42
    assert session.calls[-1][1].endswith("/api/2.2/jobs/create")


def test_reset_existing_job_instead_of_duplicating():
    def handler(method, url, kw):
        if url.endswith("/jobs/list"):
            return FakeResponse(payload={"jobs": [{"job_id": 7}]})
        return FakeResponse(payload={})

    api, session = api_with(handler)
    assert dj.create_or_update_job(api, {"name": "j"}) == 7
    method, url, kw = session.calls[-1]
    assert url.endswith("/api/2.2/jobs/reset")
    assert kw["json"] == {"job_id": 7, "new_settings": {"name": "j"}}


def test_refuses_when_job_name_is_ambiguous():
    api, _ = api_with(lambda *a: FakeResponse(payload={"jobs": [{"job_id": 1}, {"job_id": 2}]}))
    with pytest.raises(RuntimeError):
        dj.create_or_update_job(api, {"name": "j"})


def test_find_git_folder_falls_back_to_path_lookup():
    def handler(method, url, kw):
        if url.endswith("/api/2.0/repos"):
            return FakeResponse(payload={})  # empty list (Free Edition)
        if url.endswith("/scim/v2/Me"):
            return FakeResponse(payload={"userName": "me@example.com"})
        if url.endswith("/workspace/get-status"):
            assert kw["params"]["path"] == "/Users/me@example.com/bank-health-data-platform"
            return FakeResponse(payload={"resource_id": 99})
        if url.endswith("/api/2.0/repos/99"):
            return FakeResponse(payload={"id": 99, "path": "/Users/me@example.com/bank-health-data-platform"})
        raise AssertionError(url)

    api, _ = api_with(handler)
    assert dj.find_git_folder(api)["id"] == 99


def test_repr_hides_token():
    api, _ = api_with(lambda *a: FakeResponse())
    assert "dapi" not in repr(api)
