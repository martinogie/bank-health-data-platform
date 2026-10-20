from datetime import date

import pytest

from ingestion.pipeline_flags import parse_bool, should_retrain


@pytest.mark.parametrize("value", [False, "False", "false", "FALSE", " false ", "0", 0, "no", "off", "", None])
def test_false_values(value):
    assert parse_bool(value) is False


@pytest.mark.parametrize("value", [True, "True", "true", "1", 1, "yes", "on"])
def test_true_values(value):
    assert parse_bool(value) is True


@pytest.mark.parametrize("value", ["flase", "maybe", "2x"])
def test_unknown_text_raises_instead_of_guessing(value):
    with pytest.raises(ValueError):
        parse_bool(value)


def test_retrain_when_new_fdic_quarter_arrives():
    assert should_retrain("False", date(2026, 3, 31), date(2026, 6, 30)) is True


def test_no_retrain_in_ordinary_weeks():
    assert should_retrain("False", date(2026, 6, 30), date(2026, 6, 30)) is False
    assert should_retrain(False, date(2026, 6, 30), date(2026, 6, 30)) is False


def test_explicit_request_always_retrains():
    assert should_retrain("true", date(2026, 6, 30), date(2026, 6, 30)) is True


def test_first_run_does_not_force_retrain():
    assert should_retrain(False, None, date(2026, 6, 30)) is False
