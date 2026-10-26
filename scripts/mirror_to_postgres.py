"""Copy all project data into the local PostgreSQL mirror (postgres/docker-compose.yaml).

  Databricks  bank_health.{bronze,silver,gold,ml}.*  -> Postgres schemas bronze, silver, gold, ml
  Snowflake   BANK_HEALTH.{RAW,MARTS}.*              -> snowflake_raw, snowflake_marts
  FDIC API    CBLRIND + IBA flags (2020 Q1 onward)   -> explore.fdic_capital_flags (for the views)
  Views       postgres/views.sql                     -> explore.*

Each table is loaded in one transaction (drop, create, COPY in chunks, keys, indexes), so a
failure never leaves a half-copied table. Re-running skips tables whose last load matched the
current source row count (use --force to reload). Ends with a row-count check per table and
exits 1 on any mismatch. Secrets come from .env and are never printed.

Usage (from the project folder, with the postgres containers running):
    .venv\\Scripts\\python scripts/mirror_to_postgres.py                 # everything
    .venv\\Scripts\\python scripts/mirror_to_postgres.py --source snowflake
    .venv\\Scripts\\python scripts/mirror_to_postgres.py --tables "silver.*,gold.bank_quarter"
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import logging
import os
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

from _env import ROOT, load_env

sys.path.insert(0, str(ROOT))
import psycopg  # noqa: E402
import pyarrow as pa  # noqa: E402
from psycopg import sql as psql  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from ingestion import fdic  # noqa: E402
from ingestion import pg_mirror as pm  # noqa: E402

CHUNK_ROWS = 200_000
VIEWS_SQL = ROOT / "postgres" / "views.sql"
FALLBACK_NOTEBOOK = ROOT / "databricks" / "notebooks" / "tools" / "mirror_export_table.py"
log = logging.getLogger("mirror")


# ===================================================================== Postgres
def pg_connect():
    # autocommit: plain queries commit on their own, and each `with pg.transaction():` block is a
    # real BEGIN ... COMMIT (without it psycopg would nest them in one never-committed transaction)
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname="bank_health",
        autocommit=True,
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def ensure_log_table(pg):
    with pg.transaction():
        pg.execute("""create table if not exists explore.mirror_log (
            pg_table text primary key, source text not null, source_table text not null,
            source_rows bigint, pg_rows bigint, method text, seconds numeric, loaded_at timestamptz)""")


def already_loaded(pg, pg_table: str, source_rows: int) -> bool:
    row = pg.execute(
        "select source_rows, pg_rows from explore.mirror_log where pg_table = %s", (pg_table,)
    ).fetchone()
    if not row or row[0] != source_rows or row[1] != source_rows:
        return False
    schema, table = pg_table.split(".")
    exists = pg.execute("select to_regclass(%s) is not null", (pg_table,)).fetchone()[0]
    return (
        bool(exists)
        and pg.execute(f"select count(*) from {schema}.{pm.pg_ident(table)}").fetchone()[0] == source_rows
    )


def load_table(pg, desc: dict, batches, method: str) -> int:
    """Drop, create and fill one table in a single transaction. Returns the Postgres row count."""
    schema, table = desc["pg_schema"], desc["table"].lower()
    full = f"{schema}.{pm.pg_ident(table)}"
    start = time.time()
    with pg.transaction():
        cur = pg.cursor()
        cur.execute(f"drop table if exists {full} cascade")  # views are recreated at the end
        cur.execute(pm.create_table_sql(schema, table, desc["columns"]))
        names = ", ".join(pm.pg_ident(n) for n, _ in desc["columns"])
        rows = 0
        with cur.copy(f"copy {full} ({names}) from stdin with (format csv)") as cp:
            for batch in batches:
                if batch.num_rows == 0:
                    continue
                cp.write(pm.to_csv_bytes(batch, desc.get("json_columns")))
                rows += batch.num_rows
                if rows >= 1_000_000 and rows % 1_000_000 < batch.num_rows:
                    log.info("  %s: %s rows so far", full, f"{rows:,}")
        for stmt in pm.key_and_index_sql(schema, table, [n for n, _ in desc["columns"]]):
            cur.execute(stmt)
        note = f"Mirror of {desc['source']} {desc['source_table']} ({method}), loaded {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
        cur.execute(psql.SQL("comment on table {} is {}").format(psql.SQL(full), psql.Literal(note)))
        cur.execute(f"analyze {full}")
        pg_rows = cur.execute(f"select count(*) from {full}").fetchone()[0]
        cur.execute(
            """insert into explore.mirror_log values (%s, %s, %s, %s, %s, %s, %s, now())
                       on conflict (pg_table) do update set source = excluded.source, source_table = excluded.source_table,
                         source_rows = excluded.source_rows, pg_rows = excluded.pg_rows, method = excluded.method,
                         seconds = excluded.seconds, loaded_at = excluded.loaded_at""",
            (
                f"{schema}.{table}",
                desc["source"],
                desc["source_table"],
                desc["source_rows"],
                pg_rows,
                method,
                round(time.time() - start, 1),
            ),
        )
    log.info("loaded %-40s %12s rows in %5.1fs (%s)", full, f"{pg_rows:,}", time.time() - start, method)
    return pg_rows


def selected(desc: dict, patterns: list[str] | None) -> bool:
    if not patterns:
        return True
    names = {
        f"{desc['pg_schema']}.{desc['table'].lower()}",
        desc["source_table"].lower(),
        desc["table"].lower(),
    }
    return any(fnmatch.fnmatch(n, p.lower()) for p in patterns for n in names)


# ===================================================================== Databricks
class DatabricksSource:
    def __init__(self):
        from databricks import sql as dbsql

        from ingestion.databricks_files import DatabricksFiles
        from ingestion.databricks_jobs import DatabricksAPI

        self.catalog = os.environ.get("DATABRICKS_CATALOG") or "bank_health"
        self.api = DatabricksAPI.from_env()
        self.files = DatabricksFiles.from_env()
        warehouses = self.api.call("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
        wanted = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID")
        self.warehouse = (
            next((w for w in warehouses if w["id"] == wanted), warehouses[0]) if warehouses else None
        )
        if not self.warehouse:
            raise SystemExit("No Databricks SQL warehouse found")
        host = os.environ["DATABRICKS_HOST"].replace("https://", "").rstrip("/")
        self.conn = dbsql.connect(
            server_hostname=host,
            http_path=f"/sql/1.0/warehouses/{self.warehouse['id']}",
            access_token=os.environ["DATABRICKS_TOKEN"],
            use_cloud_fetch=False,
        )
        self.started_ms = int(time.time() * 1000)
        self.fallback_runs = []

    def query(self, sql: str):
        with self.conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    def dbus_today(self) -> float | None:
        try:
            return float(
                self.query(
                    "select coalesce(sum(usage_quantity), 0) from system.billing.usage "
                    "where usage_date = current_date()"
                )[0][0]
            )
        except Exception as exc:  # billing table not readable
            log.warning("could not read system.billing.usage: %s", str(exc)[:120])
            return None

    def tables(self) -> list[dict]:
        out = []
        for src_schema, pg_schema in pm.DATABRICKS_SCHEMAS.items():
            listing = self.api.call(
                "GET",
                "/api/2.1/unity-catalog/tables",
                params={"catalog_name": self.catalog, "schema_name": src_schema},
            ).get("tables", [])
            for t in sorted(listing, key=lambda t: t["name"]):
                if t.get("table_type") not in ("MANAGED", "EXTERNAL"):
                    continue  # views, volumes etc. are skipped
                cols = sorted(t.get("columns", []), key=lambda c: c.get("position", 0))
                out.append(
                    {
                        "source": "databricks",
                        "pg_schema": pg_schema,
                        "table": t["name"],
                        "source_table": f"{self.catalog}.{src_schema}.{t['name']}",
                        "columns": [
                            (
                                c["name"],
                                pm.pg_type_databricks(c.get("type_name"), c.get("type_text"), c["name"]),
                            )
                            for c in cols
                        ],
                        "json_columns": {
                            c["name"]
                            for c in cols
                            if pm.pg_type_databricks(c.get("type_name"), c.get("type_text"), c["name"])
                            == "jsonb"
                        },
                    }
                )
        return out

    def count(self, desc: dict) -> int:
        return int(self.query(f"select count(*) from {desc['source_table']}")[0][0])

    def batches(self, desc: dict):
        with self.conn.cursor() as cur:
            cur.execute(f"select * from {desc['source_table']}")
            while True:
                t = cur.fetchmany_arrow(CHUNK_ROWS)
                if t.num_rows == 0:
                    break
                yield t

    # ---------------------------------------------------------------- fallback
    def fallback_batches(self, desc: dict):
        """Write the table to Parquet with a one-off notebook run, download it, yield batches."""
        me = self.api.call("GET", "/api/2.0/preview/scim/v2/Me")["userName"]
        folder = f"/Users/{me}/.bank_health_tools"
        nb_path = f"{folder}/mirror_export_table"
        self.api.call("POST", "/api/2.0/workspace/mkdirs", json={"path": folder})
        self.api.call(
            "POST",
            "/api/2.0/workspace/import",
            json={
                "path": nb_path,
                "format": "SOURCE",
                "language": "PYTHON",
                "overwrite": True,
                "content": base64.b64encode(FALLBACK_NOTEBOOK.read_bytes()).decode(),
            },
        )
        schema, table = desc["source_table"].split(".")[1:]
        out_dir = f"/Volumes/{self.catalog}/gold/exports/_mirror/{schema}__{table}"
        run = self.api.call(
            "POST",
            "/api/2.2/jobs/runs/submit",
            json={
                "run_name": f"postgres mirror export {schema}.{table}",
                "tasks": [
                    {
                        "task_key": "export",
                        "notebook_task": {
                            "notebook_path": nb_path,
                            "source": "WORKSPACE",
                            "base_parameters": {"table": desc["source_table"], "out_dir": out_dir},
                        },
                    }
                ],
            },
        )
        log.info("  fallback: notebook run %s exporting %s to Parquet", run["run_id"], desc["source_table"])
        t0 = time.time()
        while True:
            info = self.api.call("GET", "/api/2.2/jobs/runs/get", params={"run_id": run["run_id"]})
            if info["state"]["life_cycle_state"] in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
                break
            time.sleep(15)
        self.fallback_runs.append(
            {
                "table": desc["source_table"],
                "minutes": round((time.time() - t0) / 60, 1),
                "result": info["state"].get("result_state"),
            }
        )
        if info["state"].get("result_state") != "SUCCESS":
            raise RuntimeError(f"fallback export failed: {info['state']}")
        parts = sorted(
            e["path"]
            for e in self.files.list_directory(out_dir)
            if e["path"].endswith(".parquet") and not Path(e["path"]).name.startswith(("_", "."))
        )
        with tempfile.TemporaryDirectory() as tmp:
            for i, part in enumerate(parts):
                local = self.files.download_file(part, Path(tmp) / f"p{i}.parquet")
                yield from pq.read_table(local).to_batches(CHUNK_ROWS)
                local.unlink()
        for part in self.files.list_directory(out_dir):  # remove the temporary export
            if not part.get("is_directory"):
                self.files.delete_file(part["path"])

    def compute_report(self) -> dict:
        report = {
            "warehouse": self.warehouse["name"],
            "size": self.warehouse.get("cluster_size"),
            "fallback_runs": self.fallback_runs,
        }
        try:
            resp = self.api.session.get(
                f"{self.api.host}/api/2.0/sql/history/queries",
                timeout=60,
                json={
                    "filter_by": {
                        "warehouse_ids": [self.warehouse["id"]],
                        "query_start_time_range": {
                            "start_time_ms": self.started_ms,
                            "end_time_ms": int(time.time() * 1000),
                        },
                    },
                    "max_results": 1000,
                },
            )
            queries = resp.json().get("res", []) if resp.ok else []
            report["queries"] = len(queries)
            report["query_minutes"] = round(sum(q.get("duration", 0) for q in queries) / 60000, 1)
        except Exception as exc:
            report["query_history_error"] = str(exc)[:120]
        report["dbus_today_recorded"] = self.dbus_today()
        return report


# ===================================================================== Snowflake
class SnowflakeSource:
    def __init__(self):
        from ingestion.snowflake_load import connect_from_env

        self.conn = connect_from_env()
        self.cur = self.conn.cursor()
        self.cur.execute("alter session set query_tag = 'postgres_mirror'")
        self.started = datetime.now(timezone.utc)

    def tables(self) -> list[dict]:
        self.cur.execute("""select table_schema, table_name, column_name, data_type, numeric_precision, numeric_scale
                            from BANK_HEALTH.INFORMATION_SCHEMA.COLUMNS
                            where table_schema in ('RAW', 'MARTS') order by table_schema, table_name, ordinal_position""")
        by_table: dict[tuple, list] = {}
        for schema, table, col, dtype, prec, scale in self.cur.fetchall():
            by_table.setdefault((schema, table), []).append(
                (col, pm.pg_type_snowflake(dtype, prec, scale, col))
            )
        self.cur.execute(
            "select table_schema, table_name from BANK_HEALTH.INFORMATION_SCHEMA.TABLES "
            "where table_schema in ('RAW', 'MARTS') and table_type = 'BASE TABLE'"
        )
        base = set(self.cur.fetchall())
        return [
            {
                "source": "snowflake",
                "pg_schema": pm.SNOWFLAKE_SCHEMAS[s],
                "table": t,
                "source_table": f"BANK_HEALTH.{s}.{t}",
                "columns": cols,
                "json_columns": {c for c, ty in cols if ty == "jsonb"},
            }
            for (s, t), cols in sorted(by_table.items())
            if (s, t) in base
        ]

    def count(self, desc: dict) -> int:
        self.cur.execute(f"select count(*) from {desc['source_table']}")
        return int(self.cur.fetchone()[0])

    def batches(self, desc: dict):
        cur = self.conn.cursor()
        try:
            cur.execute(f"select * from {desc['source_table']}")
            for t in cur.fetch_arrow_batches():
                yield t
        finally:
            cur.close()

    def credit_report(self, since: str | None = None) -> dict:
        """Estimate warehouse credits from this run's queries (XSMALL = 1 credit/hour, 60 s minimum
        per resume, 60 s idle before suspend). Exact figures: Snowsight > Admin > Cost Management."""
        # The connector sends bound values as text, so convert to a timestamp in SQL
        self.cur.execute(
            """select start_time, end_time from table(information_schema.query_history_by_user(
                              user_name => %s, end_time_range_start => to_timestamp_ltz(%s), result_limit => 10000))
                            where warehouse_name is not null and execution_time > 0 and query_tag = 'postgres_mirror'
                            order by start_time""",
            (os.environ["SNOWFLAKE_USER"], since or self.started.isoformat()),
        )
        blocks = []
        for s, e in self.cur.fetchall():
            if blocks and (s - blocks[-1][1]).total_seconds() <= 60:
                blocks[-1][1] = max(blocks[-1][1], e)
            else:
                blocks.append([s, e])
        billed = sum(max(60, (e - s).total_seconds() + 60) for s, e in blocks)
        return {
            "resumes": len(blocks),
            "billed_minutes": round(billed / 60, 1),
            "credits_estimate": round(billed / 3600, 3),
        }

    def close(self):
        self.conn.close()


# ===================================================================== FDIC flags
def fdic_flag_batches(latest: date):
    """CBLRIND and IBA per bank-quarter from the public FDIC API (2020 Q1 = start of CBLR)."""
    import requests

    session = requests.Session()
    certs, dates, cblr, iba = [], [], [], []
    for q in fdic.quarter_ends(date(2020, 3, 31), latest):
        for r in fdic.fetch_all(
            session, "financials", ["CERT", "REPDTE", "CBLRIND", "IBA"], filters=f"REPDTE:{fdic.to_repdte(q)}"
        ):
            certs.append(int(r["CERT"]))
            dates.append(q)
            cblr.append(None if r.get("CBLRIND") is None else int(r["CBLRIND"]))
            iba.append(None if r.get("IBA") is None else int(r["IBA"]))
    yield pa.table(
        {
            "cert": pa.array(certs, pa.int64()),
            "report_date": pa.array(dates, pa.date32()),
            "cblrind": pa.array(cblr, pa.int16()),
            "iba": pa.array(iba, pa.int16()),
        }
    )


FDIC_FLAGS = {
    "source": "fdic_api",
    "pg_schema": "explore",
    "table": "fdic_capital_flags",
    "source_table": "api.fdic.gov financials (CERT, REPDTE, CBLRIND, IBA), 2020 Q1 onward",
    "columns": [("cert", "bigint"), ("report_date", "date"), ("cblrind", "smallint"), ("iba", "smallint")],
}


# ===================================================================== views
def create_views(pg) -> str:
    sql = VIEWS_SQL.read_text(encoding="utf-8")
    needed = [
        "bronze.fdic_financials",
        "bronze.cfpb_complaints",
        "silver.fdic_financials",
        "silver.fdic_institutions",
        "silver.cfpb_complaints",
        "gold.bank_quarter",
        "ml.risk_scores",
        "snowflake_raw.fdic_financials",
        "snowflake_marts.mart_bank_scorecard",
        "ml.risk_scores_v4",
    ]
    missing = [t for t in needed if not pg.execute("select to_regclass(%s) is not null", (t,)).fetchone()[0]]
    if missing:
        return "views skipped, tables missing: " + ", ".join(missing)
    with pg.transaction():
        pg.execute(sql)
    names = [
        r[0]
        for r in pg.execute(
            "select table_name from information_schema.views where table_schema = 'explore' order by 1"
        )
    ]
    return "views created: " + ", ".join(names)


# ===================================================================== main
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", choices=["databricks", "snowflake", "all"], default="all")
    p.add_argument("--tables", help="comma-separated filters, e.g. 'silver.*,MARTS.DIM_BANK'")
    p.add_argument("--force", action="store_true", help="reload tables even if already loaded and matching")
    p.add_argument(
        "--force-fallback", help="comma-separated Databricks tables to load via the notebook fallback"
    )
    p.add_argument("--skip-fdic-flags", action="store_true")
    p.add_argument(
        "--dbu-warn",
        type=float,
        default=float(os.environ.get("MIRROR_DBU_WARN", "4.0")),
        help="stop if today's recorded Databricks DBUs are above this (default 4.0)",
    )
    p.add_argument("--ignore-quota-warning", action="store_true")
    p.add_argument(
        "--credits-since", help="ISO time to count Snowflake mirror queries from (default: this run's start)"
    )
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("snowflake", "databricks", "urllib3", "py4j", "thrift_backend"):
        logging.getLogger(noisy).setLevel(logging.ERROR)  # connectors log URLs with the account
    load_env()
    patterns = [s.strip() for s in args.tables.split(",")] if args.tables else None
    forced_fallback = {s.strip().lower() for s in (args.force_fallback or "").split(",") if s.strip()}

    pg = pg_connect()
    ensure_log_table(pg)
    results, reports, failures = [], {}, []

    def run(desc, source, method_batches):
        if not selected(desc, patterns):
            return
        pg_table = f"{desc['pg_schema']}.{desc['table'].lower()}"
        desc["source_rows"] = source.count(desc) if source else desc["source_rows"]
        if not args.force and already_loaded(pg, pg_table, desc["source_rows"]):
            log.info("skip   %-40s already loaded with %s rows", pg_table, f"{desc['source_rows']:,}")
            results.append((desc, desc["source_rows"], "skipped (already matching)"))
            return
        try:
            n = method_batches(desc)
            results.append((desc, n, None))
        except Exception as exc:  # the table's transaction was rolled back
            log.error("FAILED %s: %s", pg_table, str(exc)[:300])
            failures.append(pg_table)
            results.append((desc, None, f"failed: {str(exc)[:80]}"))

    if args.source in ("databricks", "all"):
        dbx = DatabricksSource()
        today = dbx.dbus_today()
        log.info("Databricks DBUs recorded today so far: %s (stop threshold %.1f)", today, args.dbu_warn)
        if today is not None and today > args.dbu_warn and not args.ignore_quota_warning:
            raise SystemExit(
                f"Today's Databricks usage ({today:.2f} DBU) is above {args.dbu_warn}. "
                "Stopping before copying; rerun tomorrow or with --ignore-quota-warning."
            )

        def databricks_load(desc):
            if desc["source_table"].split(".", 1)[1].lower() in forced_fallback:
                return load_table(
                    pg, desc, dbx.fallback_batches(desc), "notebook->parquet->Files API (forced)"
                )
            try:
                return load_table(pg, desc, dbx.batches(desc), "SQL warehouse, Arrow")
            except psycopg.Error:
                raise  # a Postgres problem: don't retry via Databricks
            except Exception as exc:
                log.warning(
                    "  %s via SQL warehouse failed (%s); using the notebook fallback",
                    desc["source_table"],
                    str(exc)[:160],
                )
                return load_table(pg, desc, dbx.fallback_batches(desc), "notebook->parquet->Files API")

        for desc in dbx.tables():
            run(desc, dbx, databricks_load)
        try:
            reports["databricks"] = dbx.compute_report()
        except Exception as exc:
            reports["databricks"] = {"compute_report_error": str(exc)[:160]}

    if args.source in ("snowflake", "all"):
        sf = SnowflakeSource()
        for desc in sf.tables():
            run(desc, sf, lambda d: load_table(pg, d, sf.batches(d), "Snowflake, Arrow"))
        try:
            reports["snowflake"] = sf.credit_report(args.credits_since)
        except Exception as exc:  # a report problem must not fail the copy
            reports["snowflake"] = {"credit_report_error": str(exc)[:160]}
        sf.close()

    if not args.skip_fdic_flags and args.source == "all":
        latest = fdic.latest_financials_quarter()
        flags = dict(FDIC_FLAGS)
        table = next(fdic_flag_batches(latest))
        flags["source_rows"] = table.num_rows
        run(flags, None, lambda d: load_table(pg, d, iter([table]), "FDIC API"))

    views_msg = create_views(pg)

    # ------------------------------------------------------------- report
    print("\n" + "=" * 100)
    print(f"{'Postgres table':42} {'source':10} {'source rows':>12} {'postgres rows':>14}  result")
    print("-" * 100)
    mismatches = 0
    for desc, _, note in results:
        pg_table = f"{desc['pg_schema']}.{desc['table'].lower()}"
        exists = pg.execute("select to_regclass(%s) is not null", (pg_table,)).fetchone()[0]
        pg_rows = (
            pg.execute(f"select count(*) from {desc['pg_schema']}.{pm.pg_ident(desc['table'])}").fetchone()[0]
            if exists
            else None
        )
        ok = pg_rows == desc["source_rows"]
        mismatches += 0 if ok else 1
        print(
            f"{pg_table:42} {desc['source']:10} {desc['source_rows']:>12,} {pg_rows if pg_rows is not None else '-':>14,}  "
            f"{'MATCH' if ok else 'MISMATCH'}{'  (' + note + ')' if note else ''}"
        )
    print("-" * 100)
    size = pg.execute("select pg_size_pretty(pg_database_size('bank_health'))").fetchone()[0]
    print(f"{len(results)} tables, {mismatches} mismatches · database size {size} · {views_msg}")
    for name, rep in reports.items():
        print(f"{name}: {json.dumps(rep, default=str)}")
    pg.close()
    if mismatches or failures:
        raise SystemExit(f"{mismatches} mismatch(es), {len(failures)} failure(s)")


if __name__ == "__main__":
    main()
