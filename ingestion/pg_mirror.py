"""Pure helpers for scripts/mirror_to_postgres.py (unit-tested offline).

Type mapping (Databricks / Snowflake -> PostgreSQL), keys and indexes per table, and turning
Arrow batches into CSV for COPY.
"""

from __future__ import annotations

import io
import json
import re

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv

# Source schema -> Postgres schema
DATABRICKS_SCHEMAS = {"bronze": "bronze", "silver": "silver", "gold": "gold", "ml": "ml"}
SNOWFLAKE_SCHEMAS = {"RAW": "snowflake_raw", "MARTS": "snowflake_marts"}

# Columns stored as jsonb whatever their source type
JSON_COLUMNS = {"_rescued_data"}

# Primary keys, only where the key is unique by design (bronze keeps duplicates, so it has none).
PRIMARY_KEYS = {
    "silver.fdic_financials": ("cert", "report_date"),
    "silver.fdic_institutions": ("cert",),
    "silver.fdic_failures": ("failure_id",),
    "silver.cfpb_complaints": ("complaint_id",),
    "silver.company_bank_map": ("company",),
    "gold.bank_quarter": ("cert", "report_date"),
    "ml.model_frame": ("cert", "report_date"),
    "ml.risk_scores": ("cert",),
    "snowflake_raw.fdic_financials": ("cert", "report_date"),
    "snowflake_raw.fdic_institutions": ("cert",),
    "snowflake_raw.fdic_failures": ("failure_id",),
    "snowflake_raw.cfpb_complaints": ("complaint_id",),
    "snowflake_raw.ml_risk_scores": ("cert",),
    "snowflake_marts.dim_bank": ("cert",),
    "snowflake_marts.fct_bank_quarter": ("cert", "report_date"),
    "snowflake_marts.mart_bank_scorecard": ("cert", "report_date"),
    "snowflake_marts.fct_complaints": ("complaint_id",),
    "snowflake_marts.mart_early_warning": ("cert",),
}
INDEX_COLUMNS = ("cert", "report_date", "repdte", "complaint_id")


def pg_ident(name: str) -> str:
    """Lower-case identifier, quoted only when needed."""
    n = name.lower()
    return n if re.fullmatch(r"[a-z_][a-z0-9_]*", n) else '"' + n.replace('"', '""') + '"'


# ------------------------------------------------------------------ types
def pg_type_databricks(type_name: str, type_text: str = "", column: str = "") -> str:
    """Unity Catalog column type -> Postgres type. Bronze columns are STRING, so they stay text."""
    if column.lower() in JSON_COLUMNS:
        return "jsonb"
    t = (type_name or "").upper()
    if t in ("STRING", "CHAR", "VARCHAR"):
        return "text"
    if t in ("LONG", "BIGINT"):
        return "bigint"
    if t in ("INT", "INTEGER"):
        return "integer"
    if t in ("SHORT", "SMALLINT", "BYTE", "TINYINT"):
        return "smallint"
    if t == "DOUBLE":
        return "double precision"
    if t == "FLOAT":
        return "real"
    if t == "DECIMAL":
        m = re.search(r"\((\d+)\s*,\s*(\d+)\)", type_text or "")
        return f"numeric({m.group(1)},{m.group(2)})" if m else "numeric"
    if t == "BOOLEAN":
        return "boolean"
    if t == "DATE":
        return "date"
    if t == "TIMESTAMP":
        return "timestamptz"
    if t == "TIMESTAMP_NTZ":
        return "timestamp"
    if t == "BINARY":
        return "bytea"
    if t in ("STRUCT", "ARRAY", "MAP", "VARIANT"):
        return "jsonb"
    return "text"


def pg_type_snowflake(data_type: str, precision=None, scale=None, column: str = "") -> str:
    """Snowflake INFORMATION_SCHEMA.COLUMNS type -> Postgres type."""
    if column.lower() in JSON_COLUMNS:
        return "jsonb"
    t = (data_type or "").upper()
    if t in ("NUMBER", "DECIMAL", "NUMERIC"):
        if not scale:
            return "bigint"  # whole numbers (CERT, counts, ids) fit in bigint
        return f"numeric({precision or 38},{scale})"
    if t in ("FLOAT", "DOUBLE", "REAL", "DOUBLE PRECISION"):
        return "double precision"
    if t in ("TEXT", "VARCHAR", "STRING", "CHAR"):
        return "text"
    if t == "BOOLEAN":
        return "boolean"
    if t == "DATE":
        return "date"
    if t == "TIMESTAMP_NTZ":
        return "timestamp"
    if t in ("TIMESTAMP_LTZ", "TIMESTAMP_TZ"):
        return "timestamptz"
    if t in ("VARIANT", "OBJECT", "ARRAY"):
        return "jsonb"
    if t == "BINARY":
        return "bytea"
    return "text"


# ------------------------------------------------------------------ DDL
def create_table_sql(pg_schema: str, table: str, columns: list[tuple[str, str]]) -> str:
    cols = ",\n  ".join(f"{pg_ident(n)} {t}" for n, t in columns)
    return f"create table {pg_schema}.{pg_ident(table)} (\n  {cols}\n)"


def key_and_index_sql(pg_schema: str, table: str, column_names: list[str]) -> list[str]:
    """Primary key (if defined for this table) and indexes on the usual join keys."""
    full = f"{pg_schema}.{table.lower()}"
    names = [c.lower() for c in column_names]
    stmts = []
    pk = PRIMARY_KEYS.get(full)
    if pk and all(c in names for c in pk):
        stmts.append(f"alter table {pg_schema}.{pg_ident(table)} add primary key ({', '.join(pk)})")
    for c in INDEX_COLUMNS:
        if c in names and (not pk or pk[0] != c):  # the PK already indexes its first column
            stmts.append(f"create index on {pg_schema}.{pg_ident(table)} ({c})")
    if "cert" in names and "repdte" in names:  # bronze FDIC: join key as delivered
        stmts.append(f"create index on {pg_schema}.{pg_ident(table)} (cert, repdte)")
    return stmts


# ------------------------------------------------------------------ data
def _jsonify(arr: pa.Array) -> pa.Array:
    return pa.array(
        [None if v is None else json.dumps(v, default=str) for v in arr.to_pylist()], type=pa.string()
    )


def to_csv_bytes(batch: pa.Table | pa.RecordBatch, json_columns: set[str] | None = None) -> bytes:
    """Arrow batch -> CSV for COPY ... (FORMAT csv).

    Every non-null value is quoted and nulls are left empty, so COPY tells NULL from an empty
    string. Nested values become JSON text; NUL bytes (not allowed in Postgres text) are removed.
    """
    table = batch if isinstance(batch, pa.Table) else pa.Table.from_batches([batch])
    json_columns = {c.lower() for c in (json_columns or set())}
    cols = []
    for name, col in zip(table.column_names, table.columns):
        arr = col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col
        if pa.types.is_nested(arr.type) or pa.types.is_dictionary(arr.type):
            arr = _jsonify(arr)
        elif pa.types.is_large_string(arr.type):
            arr = arr.cast(pa.string())
        if pa.types.is_string(arr.type):
            arr = pc.replace_substring(arr, "\x00", "")
            if name.lower() in json_columns:
                # rescued data is JSON already; keep it, but turn blank strings into NULL
                arr = pc.if_else(pc.equal(arr, ""), pa.scalar(None, pa.string()), arr)
        cols.append(arr)
    out = io.BytesIO()
    pacsv.write_csv(
        pa.table(cols, names=table.column_names),
        out,
        write_options=pacsv.WriteOptions(include_header=False, quoting_style="all_valid"),
    )
    return out.getvalue()
