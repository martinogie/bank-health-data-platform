"""Write, audit, publish for the Snowflake marts (finding D-02, the "publishing gap").

    prepare   clone MARTS into MARTS_NEXT (zero-copy), so dbt builds and tests the marts there;
              incremental marts arrive filled. A full refresh drops them from the clone instead.
    (dbt)     the pipeline's dbt build writes the marts into MARTS_NEXT (var marts_schema).
    publish   only if every dbt model and critical test passed: grant the readers on MARTS_NEXT,
              then swap it with MARTS in one step. Readers never lose access: the grants are on
              the schema being swapped in. Last week's marts stay in MARTS_NEXT until the next
              run (a rollback is one more swap).

Grants on a schema don't survive a clone, so publish re-applies them: USAGE on the schema and
SELECT on every table and view in it, for every reader role. Every mart exists by then (dbt built
them all in MARTS_NEXT), and nothing is created in the published MARTS afterwards, so each table is
readable the moment it is published. Schema-level FUTURE grants are not re-applied: Snowflake
allows them only with the account-wide MANAGE GRANTS privilege, even for the schema's owner (tested
in dev, 31 Oct 2026), and the pipeline role is not given that.
Swapping needs ownership of both schemas: BANK_PIPELINE_ROLE owns MARTS (snowflake/marts_ownership.sql)
and creates MARTS_NEXT itself. Pure functions over a DB-API cursor, so they are unit-tested offline.
"""

from __future__ import annotations

import re
from typing import Any

PUBLISHED = "MARTS"
NEXT = "MARTS_NEXT"
# Marts built incrementally: change detection needs their _ROW_HASH column. A clone without it
# (before the first incremental build) is dropped, so dbt rebuilds that table in full.
INCREMENTAL_MARTS = ("FCT_BANK_QUARTER", "FCT_COMPLAINTS")
ROW_HASH = "_ROW_HASH"
# Roles that read the published marts, per environment. Dev grants CI_ROLE only to prove the
# mechanism (read-only, dev data); prod also grants Power BI's reporter role.
READERS = {
    "prod": ("BANK_REPORTER_ROLE", "CI_ROLE"),
    "dev": ("CI_ROLE",),
}
_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _ident(name: str) -> str:
    """Only plain upper-case identifiers go into the SQL (no quoting tricks)."""
    if not _NAME.match(name):
        raise ValueError(f"not a plain Snowflake identifier: {name!r}")
    return name


def prepare_sql(database: str, *, full_refresh: bool, missing_row_hash: tuple[str, ...] = ()) -> list[str]:
    """Statements that set up MARTS_NEXT as a fresh clone of MARTS."""
    db = _ident(database)
    stmts = [
        f"CREATE SCHEMA IF NOT EXISTS {db}.{PUBLISHED}",  # a fresh environment has nothing to clone yet
        f"DROP SCHEMA IF EXISTS {db}.{NEXT}",
        f"CREATE SCHEMA {db}.{NEXT} CLONE {db}.{PUBLISHED}",
    ]
    rebuild = (
        INCREMENTAL_MARTS if full_refresh else tuple(t for t in INCREMENTAL_MARTS if t in missing_row_hash)
    )
    stmts += [f"DROP TABLE IF EXISTS {db}.{NEXT}.{_ident(t)}" for t in rebuild]
    return stmts


def tables_without_row_hash(cur, database: str) -> tuple[str, ...]:
    """Incremental marts in the published MARTS that have no _ROW_HASH column (or don't exist)."""
    db = _ident(database)
    cur.execute(
        f"select TABLE_NAME from {db}.INFORMATION_SCHEMA.COLUMNS "
        f"where TABLE_SCHEMA = '{PUBLISHED}' and COLUMN_NAME = '{ROW_HASH}'"
    )
    with_hash = {r[0] for r in cur.fetchall()}
    return tuple(t for t in INCREMENTAL_MARTS if t not in with_hash)


def prepare(cur, database: str, *, full_refresh: bool) -> dict[str, Any]:
    missing = tables_without_row_hash(cur, database)
    stmts = prepare_sql(database, full_refresh=full_refresh, missing_row_hash=missing)
    for s in stmts:
        cur.execute(s)
    rebuilt = INCREMENTAL_MARTS if full_refresh else missing
    return {"schema": f"{database}.{NEXT}", "full_refresh": full_refresh, "rebuilt_in_full": list(rebuilt)}


def publish_sql(database: str, readers: tuple[str, ...]) -> list[str]:
    """Grant every reader on MARTS_NEXT, then swap it with MARTS (grants first: no gap in access)."""
    db, nxt = _ident(database), f"{_ident(database)}.{NEXT}"
    stmts = []
    for role in readers:
        r = _ident(role)
        stmts += [
            f"GRANT USAGE ON SCHEMA {nxt} TO ROLE {r}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA {nxt} TO ROLE {r}",
            f"GRANT SELECT ON ALL VIEWS IN SCHEMA {nxt} TO ROLE {r}",
        ]
    stmts.append(f"ALTER SCHEMA {db}.{PUBLISHED} SWAP WITH {nxt}")
    return stmts


def publish(cur, database: str, readers: tuple[str, ...]) -> dict[str, Any]:
    for s in publish_sql(database, readers):
        cur.execute(s)
    return {
        "published": f"{database}.{PUBLISHED}",
        "readers": list(readers),
        "previous_in": f"{database}.{NEXT}",
    }


def checksums(cur, database: str, schema: str = PUBLISHED) -> dict[str, dict[str, Any]]:
    """Rows and an order-independent checksum (HASH_AGG) of every table in a schema, for the
    acceptance checks: incremental and full-refresh builds must give the same numbers."""
    db, sch = _ident(database), _ident(schema)
    cur.execute(
        f"select TABLE_NAME from {db}.INFORMATION_SCHEMA.TABLES "
        f"where TABLE_SCHEMA = '{sch}' and TABLE_TYPE = 'BASE TABLE' order by TABLE_NAME"
    )
    out = {}
    for (table,) in cur.fetchall():
        cur.execute(f"select count(*), hash_agg(*) from {db}.{sch}.{_ident(table)}")
        rows, digest = cur.fetchone()
        out[table] = {"rows": int(rows), "checksum": str(digest)}
    return out
