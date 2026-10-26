"""Small decisions the DAG makes, kept here so they can be unit-tested.

- parse_bool: DAG params can arrive as real booleans or as text ('False', 'false', '0',
  'no') depending on how the run was triggered (UI, CLI, API). Text must not be read
  as "true" just because it's a non-empty string.
- should_retrain: retrain only when asked to, or when a new FDIC quarter has arrived
  (about once a quarter); other weeks just re-score with the current @champion model.
"""

from __future__ import annotations

from datetime import date

_TRUE = {"true", "t", "1", "yes", "y", "on"}
_FALSE = {"false", "f", "0", "no", "n", "off", ""}


def parse_bool(value) -> bool:
    """True/False from a bool, number or text. Unknown text raises, rather than guessing."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"Not a true/false value: {value!r}")


def should_retrain(retrain_param, previous_quarter: date | None, latest_quarter: date) -> bool:
    """Retrain if explicitly requested, or if the FDIC has published a quarter we hadn't
    loaded before. The very first run (no previous quarter) doesn't force a retrain:
    the model is trained deliberately once, then refreshed each new quarter."""
    if parse_bool(retrain_param):
        return True
    return previous_quarter is not None and latest_quarter > previous_quarter
