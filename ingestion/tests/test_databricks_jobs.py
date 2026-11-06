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
    assert keys == ["bronze", "silver", "gold", "train_model", "score", "monitor"]
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


def jobs_api(jobs, deployment=None):
    def handler(method, url, kw):
        assert method == "GET"  # the pipeline only reads the job now
        if url.endswith("/jobs/list"):
            return FakeResponse(payload={"jobs": jobs} if jobs else {})
        if url.endswith("/jobs/get"):
            return FakeResponse(payload={"settings": {"deployment": deployment}})
        raise AssertionError(url)

    return api_with(handler)


def test_existing_job_is_found_and_never_changed():
    api, session = jobs_api([{"job_id": 7}], deployment={"kind": "BUNDLE"})
    assert dj.existing_job_id(api, "j") == 7
    assert all(method == "GET" for method, _url, _kw in session.calls)


def test_a_missing_job_says_to_deploy_the_bundle():
    api, _ = jobs_api([])
    with pytest.raises(RuntimeError, match="deploy the bundle"):
        dj.existing_job_id(api, "j")


def test_refuses_when_job_name_is_ambiguous():
    api, _ = jobs_api([{"job_id": 1}, {"job_id": 2}])
    with pytest.raises(RuntimeError, match="expected exactly one"):
        dj.existing_job_id(api, "j")


def test_databricks_yml_matches_the_job_definition_in_code():
    """databricks.yml (the bundle) must define the same job as job_settings: same tasks in the same
    chain, notebooks, timeouts, retries, environment, parameters and safety settings."""
    from pathlib import Path

    import yaml

    from ingestion.environment import SETTINGS

    bundle = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "databricks.yml").read_text(encoding="utf-8")
    )
    job = bundle["resources"]["jobs"]["pipeline"]
    ref = dj.job_settings("ROOT", SETTINGS["dev"])
    assert [t["task_key"] for t in job["tasks"]] == [t["task_key"] for t in ref["tasks"]]
    for y, r in zip(job["tasks"], ref["tasks"]):
        assert (
            y["notebook_task"]["notebook_path"].split("/")[-1]
            == r["notebook_task"]["notebook_path"].split("/")[-1]
        )
        assert y["notebook_task"]["source"] == r["notebook_task"]["source"]
        assert (y["timeout_seconds"], y["max_retries"], y["environment_key"]) == (
            r["timeout_seconds"], r["max_retries"], r["environment_key"])  # fmt: skip
        assert y.get("depends_on") == r.get("depends_on")
    assert job["environments"] == ref["environments"]
    assert (job["max_concurrent_runs"], job["timeout_seconds"], job["queue"]) == (
        ref["max_concurrent_runs"], ref["timeout_seconds"], ref["queue"])  # fmt: skip
    assert job["description"] == ref["description"]
    assert [p["name"] for p in job["parameters"]] == [p["name"] for p in ref["parameters"]]
    assert {"name": "retrain", "default": "false"} in job["parameters"]
    for target, env in (("dev", "dev"), ("prod", "prod")):
        v, s = bundle["targets"][target]["variables"], SETTINGS[env]
        assert (v["env"], v["catalog"], v["job_name"]) == (
            s.name,
            s.databricks_catalog,
            s.databricks_job_name,
        )
        assert v["notebook_root"].endswith(f"/{s.databricks_folder}/databricks/notebooks")


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
    assert dj.find_git_folder(api, "bank-health-data-platform")["id"] == 99


def test_repr_hides_token():
    api, _ = api_with(lambda *a: FakeResponse())
    assert "dapi" not in repr(api)
