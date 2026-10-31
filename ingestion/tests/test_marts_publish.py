"""Write, audit, publish (ingestion/marts_publish.py): the SQL it sends, with Snowflake faked."""

from __future__ import annotations

import pytest

from ingestion import marts_publish as mp


class Cur:
    def __init__(self, columns_with_hash=(), tables=()):
        self.sql, self.columns_with_hash, self.tables, self._rows = [], columns_with_hash, tables, []

    def execute(self, sql):
        self.sql.append(sql)
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            self._rows = [(t,) for t in self.columns_with_hash]
        elif "INFORMATION_SCHEMA.TABLES" in sql:
            self._rows = [(t,) for t in self.tables]
        elif sql.startswith("select count(*), hash_agg(*)"):
            self._rows = [(10, -123)]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]


def test_prepare_clones_the_published_marts():
    cur = Cur(columns_with_hash=mp.INCREMENTAL_MARTS)
    out = mp.prepare(cur, "BANK_HEALTH", full_refresh=False)
    assert cur.sql[1:] == [
        "CREATE SCHEMA IF NOT EXISTS BANK_HEALTH.MARTS",
        "DROP SCHEMA IF EXISTS BANK_HEALTH.MARTS_NEXT",
        "CREATE SCHEMA BANK_HEALTH.MARTS_NEXT CLONE BANK_HEALTH.MARTS",
    ]
    assert out["rebuilt_in_full"] == []


def test_a_full_refresh_drops_the_incremental_marts_from_the_clone():
    cur = Cur(columns_with_hash=mp.INCREMENTAL_MARTS)
    out = mp.prepare(cur, "BANK_HEALTH", full_refresh=True)
    assert cur.sql[-2:] == [
        "DROP TABLE IF EXISTS BANK_HEALTH.MARTS_NEXT.FCT_BANK_QUARTER",
        "DROP TABLE IF EXISTS BANK_HEALTH.MARTS_NEXT.FCT_COMPLAINTS",
    ]
    assert out["rebuilt_in_full"] == ["FCT_BANK_QUARTER", "FCT_COMPLAINTS"]


def test_a_mart_without_its_row_hash_is_rebuilt_in_full():
    """The first run after deploying incremental marts: the old tables have no _ROW_HASH."""
    cur = Cur(columns_with_hash=("FCT_COMPLAINTS",))
    out = mp.prepare(cur, "BANK_HEALTH", full_refresh=False)
    assert cur.sql[-1] == "DROP TABLE IF EXISTS BANK_HEALTH.MARTS_NEXT.FCT_BANK_QUARTER"
    assert out["rebuilt_in_full"] == ["FCT_BANK_QUARTER"]


def test_publish_grants_every_reader_before_the_swap():
    cur = Cur()
    mp.publish(cur, "BANK_HEALTH", mp.READERS["prod"])
    swap = "ALTER SCHEMA BANK_HEALTH.MARTS SWAP WITH BANK_HEALTH.MARTS_NEXT"
    assert cur.sql[-1] == swap and cur.sql.count(swap) == 1
    for role in ("BANK_REPORTER_ROLE", "CI_ROLE"):
        for grant in (
            f"GRANT USAGE ON SCHEMA BANK_HEALTH.MARTS_NEXT TO ROLE {role}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA BANK_HEALTH.MARTS_NEXT TO ROLE {role}",
            f"GRANT SELECT ON ALL VIEWS IN SCHEMA BANK_HEALTH.MARTS_NEXT TO ROLE {role}",
        ):
            assert cur.sql.index(grant) < cur.sql.index(swap)


def test_publish_never_needs_manage_grants():
    """Schema-level FUTURE grants need the account-wide MANAGE GRANTS privilege (even for the
    schema owner), which the pipeline role doesn't have: publish must not send them."""
    assert not any("FUTURE" in s for s in mp.publish_sql("BANK_HEALTH", mp.READERS["prod"]))


def test_prod_readers_are_power_bi_and_ci():
    assert set(mp.READERS["prod"]) == {"BANK_REPORTER_ROLE", "CI_ROLE"}


@pytest.mark.parametrize("bad", ["BANK_HEALTH; DROP", "bank_health", '"X"', "A B"])
def test_only_plain_identifiers_reach_the_sql(bad):
    with pytest.raises(ValueError, match="plain Snowflake identifier"):
        mp.publish_sql(bad, ("CI_ROLE",))


def test_checksums_cover_every_table():
    cur = Cur(tables=("DIM_BANK", "FCT_COMPLAINTS"))
    assert mp.checksums(cur, "BANK_HEALTH_DEV") == {
        "DIM_BANK": {"rows": 10, "checksum": "-123"},
        "FCT_COMPLAINTS": {"rows": 10, "checksum": "-123"},
    }
