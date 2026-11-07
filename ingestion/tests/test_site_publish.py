"""ingestion/site_export.py and ingestion/site_publish.py, offline: a tiny run builds valid files; a
resumed run keeps its failed attempt; checks block the upload; pointer, retention and rollback."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from ingestion import site_export as sx
from ingestion import site_publish as sp


# ------------------------------------------------------------------ a tiny real-shaped run
def tiny_inputs():
    quarters = [
        f"{y} Q{q}"
        for y, q in [(2024, 3), (2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]
    ]
    hist = {k: [1.0] * 8 for k in ("ncl", "t1", "roa", "eff", "dep")}
    banks = [
        {"c": 1, "n": "Alpha Bank", "ci": "Austin", "st": "TX", "a": 6.475, "age": 40.0, "t": 1, "p": 0.81, "b": "H",
         "pp": 99, "r": ["Size (total assets): $6m (median of similar-size banks: $68m)"], "h": hist, "wf": 1},
        {"c": 2, "n": "Beta Bank", "ci": "Denver", "st": "CO", "a": 120.0, "age": 2.0, "t": 2, "p": None, "b": "N",
         "pp": None, "r": [], "h": hist},
    ]  # fmt: skip
    watchlist = {"meta": {"quarter": "2026-06-30", "quarterLabel": "2026 Q2", "modelVersion": 5, "history": quarters},
                 "banks": banks}  # fmt: skip
    waterfalls = {1: {"c": 1, "q": "2026-06-30", "w": {"b": 5.5, "o": 1.0, "i": [
        {"f": "log_total_assets", "l": "Size", "v": 15.68, "m": 18.03, "pp": 3.0},
        {"f": "noncurrent_loans_pct", "l": "Non-current loans", "v": 2.0, "m": 0.5, "pp": 10.0},
        {"f": "roa_pct", "l": "Return on assets", "v": -1.0, "m": 1.0, "pp": 4.0}]}}}  # fmt: skip
    ni_rows = [
        {"cert": 1, "report_date": date(2026, 6, 30), "net_income_qtr_k": -3.0, "total_assets_k": 6475.0}
    ]
    rows = {"bronze": {"fdic_financials": 10, "fdic_institutions": 4, "fdic_failures": 3, "cfpb_complaints": 9},
            "silver": {"fdic_financials": 8, "fdic_institutions": 2, "fdic_failures": 1, "cfpb_complaints": 7},
            "gold": {"bank_quarter": 8}, "marts": {"DIM_BANK": 2}, "match": {"complaints_matched_to_bank_pct": 34.2}}  # fmt: skip
    run = {"run_id": "scheduled__2026-11-09T12:00:00+00:00", "started_at": datetime(2026, 11, 9, 12, tzinfo=UTC),
           "ended_at": datetime(2026, 11, 9, 12, 16, tzinfo=UTC), "duration_seconds": 960, "rows_detail": json.dumps(rows),
           "dbt_models_ok": 12, "dbt_tests_pass": 97, "dbt_tests_fail": 0, "databricks_minutes": 7.5}  # fmt: skip
    health = [
        {"run_id": run["run_id"], "started_at": run["started_at"], "ended_at": run["ended_at"], "duration_minutes": 16.0,
         "state": "success", "dbt_tests_warn": 2, "databricks_minutes": 7.5, "credits": None, "scored_banks": 1,
         "resumed": False, "earlier_attempts": None},
        {"run_id": "manual__2026-11-06T09:40", "started_at": datetime(2026, 11, 6, 9, 40, 34, tzinfo=UTC),
         "ended_at": datetime(2026, 11, 6, 10, 39, 10, tzinfo=UTC), "duration_minutes": 58.6, "state": "success",
         "dbt_tests_warn": 2, "databricks_minutes": 7.2, "credits": None, "scored_banks": 1, "resumed": True,
         "earlier_attempts": json.dumps([{"state": "failed", "started_at": "2026-11-06T09:40:34+00:00",
                                          "ended_at": "2026-11-06T10:06:41+00:00", "failed_tasks": "export_watchlist"}])},
    ]  # fmt: skip
    return watchlist, waterfalls, ni_rows, run, health


def build(**kw):
    w, wf, ni, run, health = tiny_inputs()
    return sx.build_site(w, wf, ni, run, health, sx.load_model(), fixture=False, **kw)


def test_a_real_run_builds_every_file_and_they_pass_the_schemas():
    docs = build()
    assert set(docs) == {
        "overview.json",
        "banks_index.json",
        "model.json",
        "pipeline.json",
        "banks/1.json",
        "banks/2.json",
    }
    assert sp.validate(docs) == []
    assert all(d["fixture"] is False for d in docs.values())
    assert docs["overview.json"]["dataAsOf"] == {"quarterLabel": "2026 Q2", "updated": "2026-11-09"}
    assert "preview" not in docs["banks_index.json"]  # only Stage A lists preview banks


def test_bank_text_is_generated_with_one_rounding_rule():
    bank = build()["banks/1.json"]
    assert bank["note"] == {"text": "2026 Q2: net loss of $3k in the quarter (−0.05% of assets), on total assets of $6.5m.",
                            "source": "FDIC call report, 2026 Q2"}  # fmt: skip
    assert bank["reasons"][0]["text"] == "Size (total assets): $6.5m (median of similar-size banks: $68m)"
    assert bank["pushes"] == {"credit": 10.0, "earnings": 4.0} and bank["driver"] == "credit"
    assert build()["banks/2.json"]["flags"] == ["newBank"]


def test_a_resumed_run_is_one_entry_with_its_failed_attempt():
    runs = build()["pipeline.json"]["runs"]
    assert runs[0]["resumed"] is False and runs[0]["earlierAttempts"] == []
    assert runs[1] == {"date": "2026-11-06", "state": "success", "durationSeconds": 3516, "databricksMinutes": 7.2,
                       "credits": None, "banksScored": 1, "resumed": True,
                       "earlierAttempts": [{"state": "failed", "failedTasks": "export_watchlist", "durationSeconds": 1567}]}  # fmt: skip


def test_money_rule_and_run_keys():
    assert [sx.money_m(x) for x in (6.475, 68.5, 34_900.3, 0.003)] == ["$6.5m", "$69m", "$34.9bn", "$3k"]
    key = sp.run_key("scheduled__2026-11-09T12:00:00+00:00", "2026-11-09T12:00:01+00:00")
    assert sp.RUN_KEY.match(key) and key.startswith("20261109T120001Z-")


# ------------------------------------------------------------------ R2, faked
class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.meta: dict[str, dict] = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key], self.meta[Key] = Body, kw

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(f"NoSuchKey: {Key}")

        class B:
            def __init__(s, d):
                s.d = d

            def read(s):
                return s.d

        return {"Body": B(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix, **kw):
        return {
            "Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)],
            "IsTruncated": False,
        }

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)


DBT = {"dbt/index.html": b"<html>docs</html>", "dbt/manifest.json": b"{}", "dbt/catalog.json": b"{}"}


def test_publish_uploads_then_switches_the_pointer(monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    s3, docs = FakeS3(), build()
    out = sp.publish(s3, "", "20261109T120000Z-abc123", docs, DBT, {"quarter": "2026 Q2"})
    assert out["files"] == len(docs) + 3 and out["previous"] is None
    assert json.loads(s3.objects["current.json"])["run"] == "20261109T120000Z-abc123"
    assert "runs/20261109T120000Z-abc123/data/banks/1.json" in s3.objects
    assert s3.meta["current.json"]["CacheControl"] == "no-cache"
    assert s3.meta["runs/20261109T120000Z-abc123/dbt/index.html"]["ContentType"].startswith("text/html")


def test_a_schema_problem_or_an_identifier_stops_before_anything_is_uploaded(monkeypatch):
    s3, docs = FakeS3(), build()
    bad = {**docs, "overview.json": {**docs["overview.json"], "bandCounts": {}}}
    with pytest.raises(sp.PublishRefused, match="schema"):
        sp.publish(s3, "", "20261109T120000Z-abc123", bad, DBT, {})
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "ORGX-ACCT99")
    with pytest.raises(sp.PublishRefused, match="identifier scan"):
        sp.publish(
            s3, "", "20261109T120000Z-abc123", docs, {**DBT, "dbt/manifest.json": b'{"a": "orgx-acct99"}'}, {}
        )
    assert s3.objects == {}  # nothing went up, the live site is untouched


def test_retention_keeps_the_newest_five_and_never_the_current_one():
    s3 = FakeS3()
    keys = [f"202611{d:02d}T120000Z-aaaaaa" for d in range(1, 8)]
    for k in keys:
        s3.put_object(Bucket="b", Key=f"runs/{k}/data/overview.json", Body=b"{}")
    deleted = sp.prune(s3, "", current=keys[0])  # current is the oldest (after a rollback)
    assert keys[0] not in deleted and deleted == keys[1:2]
    assert sp.list_runs(s3, "") == [keys[0], *keys[2:]]


def test_rollback_points_at_an_earlier_run_and_refuses_unknown_ones(monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    s3, docs = FakeS3(), build()
    sp.publish(s3, "dev/", "20261102T120000Z-aaaaaa", docs, DBT, {"quarter": "2026 Q2"})
    sp.publish(s3, "dev/", "20261109T120000Z-bbbbbb", docs, DBT, {"quarter": "2026 Q2"})
    assert json.loads(s3.objects["dev/current.json"])["run"] == "20261109T120000Z-bbbbbb"
    doc = sp.rollback(s3, "dev/", "20261102T120000Z-aaaaaa")
    assert doc["run"] == "20261102T120000Z-aaaaaa" and doc["rolledBackFrom"] == "20261109T120000Z-bbbbbb"
    with pytest.raises(sp.PublishRefused):
        sp.rollback(s3, "dev/", "20990101T000000Z-cccccc")
    assert not any(k.startswith("runs/") for k in s3.objects)  # dev never writes the prod root


def test_dbt_docs_are_read_only_when_complete(tmp_path):
    assert sp.dbt_docs(tmp_path) == {}
    for f in sp.DBT_FILES:
        (tmp_path / f).write_text("x")
    assert set(sp.dbt_docs(tmp_path)) == {"dbt/index.html", "dbt/manifest.json", "dbt/catalog.json"}
