"""Table snapshots for the D-02 acceptance runs: are two runs' tables identical?

A snapshot = for each table, its row count, distinct keys and an order-independent checksum of
every column except the lineage and load-time ones (they say where and when a row was loaded,
not what it says). Databricks layers come from the notebook tools/acceptance_checksums; Snowflake
RAW and MARTS from `snowflake_checksums` below (HASH_AGG, which ignores row order).

What must match between two snapshots:
- bronze ("keys"): distinct keys only, since bronze is append-only and grows on every re-run;
- every later table ("all"): rows, distinct keys, checksum and the column list.
"""

from __future__ import annotations

LINEAGE = {"_SOURCE_FILE", "_INGESTED_AT", "_FILE_MODIFIED_AT", "_LOADED_AT", "SCORED_AT"}
SNOWFLAKE_TABLES = {
    "RAW.FDIC_FINANCIALS": ["CERT", "REPORT_DATE"],
    "RAW.FDIC_INSTITUTIONS": ["CERT"],
    "RAW.FDIC_FAILURES": ["FAILURE_ID"],
    "RAW.CFPB_COMPLAINTS": ["COMPLAINT_ID"],
    "RAW.ML_RISK_SCORES": ["CERT"],
    "MARTS.DIM_BANK": ["CERT"],
    "MARTS.FCT_BANK_QUARTER": ["CERT", "REPORT_DATE"],
    "MARTS.FCT_COMPLAINTS": ["COMPLAINT_ID"],
    "MARTS.MART_BANK_SCORECARD": ["CERT", "REPORT_DATE"],
    "MARTS.MART_EARLY_WARNING": ["CERT"],
}
COMPARED = {"keys": ("distinct_keys",), "all": ("rows", "distinct_keys", "checksum", "columns")}


def checksum_sql(database: str, table: str, keys: list[str], columns: list[str]) -> str:
    """One query: rows, distinct keys, HASH_AGG over the non-lineage columns."""
    cols = ", ".join(f'"{c}"' for c in columns)
    key_cols = ", ".join(f'"{k}"' for k in keys)
    return f"select count(*), count(distinct {key_cols}), hash_agg({cols}) from {database}.{table}"


def snowflake_checksums(cur, database: str, tables: dict[str, list[str]] = SNOWFLAKE_TABLES) -> dict:
    """{"SNOWFLAKE.<schema>.<table>": {rows, distinct_keys, checksum, columns, compare}}. Read-only."""
    out = {}
    for table, keys in tables.items():
        schema, name = table.split(".")
        cur.execute(
            f"select COLUMN_NAME from {database}.INFORMATION_SCHEMA.COLUMNS "
            "where TABLE_SCHEMA = %(s)s and TABLE_NAME = %(t)s order by COLUMN_NAME",
            {"s": schema, "t": name},
        )
        columns = [r[0] for r in cur.fetchall() if r[0].upper() not in LINEAGE]
        if not columns:
            out[f"SNOWFLAKE.{table}"] = {"missing": True, "compare": "all"}
            continue
        cur.execute(checksum_sql(database, table, keys, columns))
        rows, distinct_keys, checksum = cur.fetchone()
        out[f"SNOWFLAKE.{table}"] = {
            "rows": int(rows),
            "distinct_keys": int(distinct_keys),
            "checksum": str(checksum),
            "columns": columns,
            "compare": "all",
        }
    return out


def differences(a: dict, b: dict) -> dict[str, dict]:
    """{table: {field: (in a, in b)}} for every table whose compared fields differ, or that is in
    only one snapshot. Empty = identical."""
    out = {}
    for table in sorted(set(a) | set(b)):
        if table not in a or table not in b:
            out[table] = {"present": (table in a, table in b)}
            continue
        x, y = a[table], b[table]
        if x.get("missing") or y.get("missing"):
            if bool(x.get("missing")) != bool(y.get("missing")):
                out[table] = {"missing": (bool(x.get("missing")), bool(y.get("missing")))}
            continue
        fields = COMPARED[x.get("compare", "all")]
        diff = {f: (x.get(f), y.get(f)) for f in fields if x.get(f) != y.get(f)}
        if diff:
            out[table] = diff
    return out


def summary_lines(a: dict, b: dict) -> list[str]:
    """One line per table: what was compared, and whether it matched."""
    diff = differences(a, b)
    lines = []
    for table in sorted(set(a) | set(b)):
        entry = a.get(table) or b.get(table) or {}
        what = "distinct keys" if entry.get("compare") == "keys" else "rows, keys, checksum"
        if table in diff:
            lines.append(f"DIFFERENT  {table}: {diff[table]}")
        elif entry.get("missing"):
            lines.append(f"(missing)  {table}: not in either snapshot")
        else:
            rows = f"{b[table].get('rows', 0):,} rows" if entry.get("compare") == "all" else ""
            keys = f"{b[table].get('distinct_keys', 0):,} keys"
            lines.append(f"identical  {table}: {', '.join(x for x in (rows, keys) if x)} ({what})")
    return lines
