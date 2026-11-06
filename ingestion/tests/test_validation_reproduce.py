"""scripts/validation_reproduce.py: offline, with a fake Databricks API."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import validation_reproduce as vr  # noqa: E402

EXPECTED = json.loads((ROOT / "docs" / "validation" / "expected_v5.json").read_text(encoding="utf-8"))


class FakeAPI:
    def __init__(self, metrics, tags, version="5"):
        self.metrics, self.tags, self.version = metrics, tags, version

    def call(self, method, path, params=None, **_):
        assert method == "GET"  # read-only
        if "/aliases/" in path:
            return {"version": self.version, "run_id": "run1"}
        if path.endswith("model-versions/get"):
            return {"model_version": {"tags": [{"key": k, "value": v} for k, v in self.tags.items()]}}
        if path.endswith("runs/get"):
            return {"run": {"data": {"metrics": [{"key": k, "value": v} for k, v in self.metrics.items()]}}}
        raise AssertionError(path)


CARD_METRICS = {"test_roc_auc": 0.851533, "test_pr_auc": 0.333288, "test_top_decile_capture": 0.52594,
                "test_brier": 0.043625}  # fmt: skip
CARD_TAGS = {"band_cutoff_high": "0.358027", "band_cutoff_elevated": "0.162898",
             "band_cutoff_watch": "0.035561", "band_method": "text"}  # fmt: skip


def test_matching_records_pass(capsys):
    assert vr.main([], api=FakeAPI(CARD_METRICS, CARD_TAGS)) == 0
    assert "all figures match" in capsys.readouterr().out


def test_a_run_metric_that_disagrees_with_the_tag_is_a_note_not_a_failure(capsys):
    metrics = dict(CARD_METRICS, band_cutoff_elevated=0.0633)
    assert vr.main([], api=FakeAPI(metrics, CARD_TAGS)) == 0
    out = capsys.readouterr().out
    assert "NOTE band_cutoff_elevated: the training run also logged 0.063300" in out


def test_a_wrong_figure_or_version_fails(capsys):
    metrics = dict(CARD_METRICS, test_pr_auc=0.30)
    assert vr.main([], api=FakeAPI(metrics, CARD_TAGS, version="6")) == 1
    out = capsys.readouterr().out
    assert "FAIL test_pr_auc" in out and "alias points at 6" in out


def test_a_missing_tag_fails(capsys):
    tags = {k: v for k, v in CARD_TAGS.items() if k != "band_cutoff_watch"}
    assert vr.main([], api=FakeAPI(CARD_METRICS, tags)) == 1
    assert "no version_tag named band_cutoff_watch" in capsys.readouterr().out


def test_expected_file_names_every_figure_with_a_record():
    for spec in EXPECTED["metrics"].values():
        assert spec["where"] in ("run_metric", "version_tag") and spec["tolerance"] > 0
