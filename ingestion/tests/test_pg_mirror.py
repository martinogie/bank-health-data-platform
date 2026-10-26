import csv
import io
from datetime import UTC, date, datetime

import pyarrow as pa
import pytest

from ingestion import pg_mirror as pm


@pytest.mark.parametrize(
    "type_name, type_text, column, expected",
    [
        ("STRING", "string", "cert", "text"),  # bronze stays text
        ("LONG", "bigint", "complaint_id", "bigint"),
        ("INT", "int", "cert", "integer"),
        ("DOUBLE", "double", "roa_pct", "double precision"),
        ("DECIMAL", "decimal(10,2)", "x", "numeric(10,2)"),
        ("DATE", "date", "report_date", "date"),
        ("TIMESTAMP", "timestamp", "_ingested_at", "timestamptz"),
        ("TIMESTAMP_NTZ", "timestamp_ntz", "x", "timestamp"),
        ("BOOLEAN", "boolean", "is_active", "boolean"),
        ("STRUCT", "struct<a:int>", "x", "jsonb"),
        ("ARRAY", "array<string>", "x", "jsonb"),
        ("STRING", "string", "_rescued_data", "jsonb"),  # rescued data is JSON
    ],
)
def test_databricks_type_mapping(type_name, type_text, column, expected):
    assert pm.pg_type_databricks(type_name, type_text, column) == expected


@pytest.mark.parametrize(
    "data_type, precision, scale, expected",
    [
        ("NUMBER", 38, 0, "bigint"),
        ("NUMBER", 10, 2, "numeric(10,2)"),
        ("FLOAT", None, None, "double precision"),
        ("TEXT", None, None, "text"),
        ("BOOLEAN", None, None, "boolean"),
        ("DATE", None, None, "date"),
        ("TIMESTAMP_NTZ", None, None, "timestamp"),
        ("TIMESTAMP_LTZ", None, None, "timestamptz"),
        ("VARIANT", None, None, "jsonb"),
    ],
)
def test_snowflake_type_mapping(data_type, precision, scale, expected):
    assert pm.pg_type_snowflake(data_type, precision, scale) == expected


def test_identifiers_lower_case_and_quoted_only_when_needed():
    assert pm.pg_ident("REPORT_DATE") == "report_date"
    assert pm.pg_ident("Sub-product") == '"sub-product"'


def test_keys_and_indexes():
    stmts = pm.key_and_index_sql("silver", "fdic_financials", ["cert", "report_date", "roa_pct"])
    assert stmts[0] == "alter table silver.fdic_financials add primary key (cert, report_date)"
    assert "create index on silver.fdic_financials (report_date)" in stmts
    assert not any("(cert)" in s for s in stmts)  # PK already indexes cert


def test_bronze_has_no_primary_key_but_indexes():
    stmts = pm.key_and_index_sql("bronze", "fdic_financials", ["cert", "repdte", "_rescued_data"])
    assert not any("primary key" in s for s in stmts)
    assert "create index on bronze.fdic_financials (cert, repdte)" in stmts


def test_snowflake_upper_case_columns_get_keys():
    stmts = pm.key_and_index_sql("snowflake_marts", "FCT_COMPLAINTS", ["COMPLAINT_ID", "CERT"])
    assert stmts[0] == "alter table snowflake_marts.fct_complaints add primary key (complaint_id)"


def parse(csv_bytes):
    return list(csv.reader(io.StringIO(csv_bytes.decode("utf-8"))))


def test_csv_distinguishes_null_from_empty_and_keeps_text():
    t = pa.table({"a": ["x", "", None], "b": [1, None, 3]})
    text = pm.to_csv_bytes(t).decode()
    lines = text.splitlines()
    assert lines[0] == '"x","1"'  # every non-null value quoted (COPY accepts quoted numbers)
    assert lines[1] == '"",'  # empty string quoted, NULL left empty
    assert lines[2] == ',"3"'


def test_csv_handles_dates_timestamps_bools_newlines_and_nul():
    t = pa.table(
        {
            "d": [date(2026, 6, 30)],
            "ts": pa.array([datetime(2026, 10, 20, 17, 34, 27, tzinfo=UTC)], pa.timestamp("us", tz="UTC")),
            "flag": [True],
            "txt": ["line1\nline2\x00"],
        }
    )
    row = parse(pm.to_csv_bytes(t))[0]
    assert row[0] == "2026-06-30"
    assert row[1].startswith("2026-10-20 17:34:27")
    assert row[2] == "true"
    assert row[3] == "line1\nline2"  # newline kept inside quotes, NUL removed


def test_nested_values_become_json_and_blank_rescued_data_null():
    t = pa.table(
        {"s": pa.array([{"a": 1}, None], pa.struct([("a", pa.int64())])), "_rescued_data": ['{"x":"1"}', ""]}
    )
    rows = parse(pm.to_csv_bytes(t, {"_rescued_data"}))
    assert rows[0] == ['{"a": 1}', '{"x":"1"}']
    assert rows[1] == ["", ""]


def test_create_table_sql():
    sql = pm.create_table_sql("gold", "Bank_Quarter", [("CERT", "integer"), ("Report Date", "date")])
    assert sql.startswith("create table gold.bank_quarter (")
    assert '"report date" date' in sql
