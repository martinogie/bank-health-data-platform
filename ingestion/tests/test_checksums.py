"""Acceptance snapshots: what counts as identical (offline, fake Snowflake cursor)."""

from ingestion import checksums


def entry(rows=10, keys=10, checksum="123", columns=("A", "B"), compare="all"):
    return {
        "rows": rows,
        "distinct_keys": keys,
        "checksum": checksum,
        "columns": list(columns),
        "compare": compare,
    }


def test_identical_snapshots_have_no_differences():
    a = {"SNOWFLAKE.MARTS.DIM_BANK": entry(), "DATABRICKS.x.bronze.t": entry(compare="keys")}
    assert checksums.differences(a, dict(a)) == {}


def test_bronze_is_compared_on_distinct_keys_only():
    a = {"bronze": entry(rows=100, checksum="1", compare="keys")}
    b = {"bronze": entry(rows=150, checksum="2", compare="keys")}  # re-run appended 50 rows
    assert checksums.differences(a, b) == {}
    b["bronze"]["distinct_keys"] = 11  # a new key appeared
    assert checksums.differences(a, b) == {"bronze": {"distinct_keys": (10, 11)}}


def test_later_tables_must_match_on_rows_checksum_and_columns():
    a, b = {"silver": entry()}, {"silver": entry(checksum="999", columns=("A", "B", "C"))}
    assert checksums.differences(a, b) == {
        "silver": {"checksum": ("123", "999"), "columns": (["A", "B"], ["A", "B", "C"])}
    }


def test_a_table_in_only_one_snapshot_is_a_difference():
    assert checksums.differences({"t": entry()}, {}) == {"t": {"present": (True, False)}}
    assert checksums.differences({"t": {"missing": True}}, {"t": {"missing": True}}) == {}
    assert checksums.differences({"t": {"missing": True}}, {"t": entry()}) == {
        "t": {"missing": (True, False)}
    }


def test_summary_lines_name_each_table():
    a = {"silver": entry(), "bronze": entry(compare="keys")}
    lines = checksums.summary_lines(a, dict(a))
    assert lines == [
        "identical  bronze: 10 keys (distinct keys)",
        "identical  silver: 10 rows, 10 keys (rows, keys, checksum)",
    ]


class FakeCursor:
    def __init__(self):
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        self.last = sql

    def fetchall(self):  # the column list
        return [("CERT",), ("NAME",), ("_LOADED_AT",), ("_INGESTED_AT",), ("SCORED_AT",)]

    def fetchone(self):
        return (5, 5, -42)


def test_snowflake_checksums_leave_out_lineage_columns():
    cur = FakeCursor()
    out = checksums.snowflake_checksums(cur, "BANK_HEALTH_DEV", {"MARTS.DIM_BANK": ["CERT"]})
    assert out == {
        "SNOWFLAKE.MARTS.DIM_BANK": {
            "rows": 5, "distinct_keys": 5, "checksum": "-42", "columns": ["CERT", "NAME"], "compare": "all",
        }
    }  # fmt: skip
    assert cur.queries[1][0] == (
        'select count(*), count(distinct "CERT"), hash_agg("CERT", "NAME") from BANK_HEALTH_DEV.MARTS.DIM_BANK'
    )
