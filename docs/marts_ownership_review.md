# Review: who owns `MARTS` (Phase 4, PR 10; written review, no change made)

## What is true today (read from prod on 6 Nov 2026, `SHOW` commands)

| Object | Owner | Others |
|---|---|---|
| Schema `BANK_HEALTH.MARTS` | `BANK_PIPELINE_ROLE` | `BANK_REPORTER_ROLE` (Power BI) and `CI_ROLE`: USAGE |
| Schema `BANK_HEALTH.MARTS_NEXT` | `BANK_PIPELINE_ROLE` | same as `MARTS` |
| The 5 tables in each (e.g. `DIM_BANK`) | `BANK_PIPELINE_ROLE` | `BANK_REPORTER_ROLE`, `CI_ROLE`: SELECT |
| Future grants in `MARTS` (schema level) | none: they don't survive a swap | readers get SELECT from each publish (it grants SELECT on every mart before the swap) and from **database-level** future grants for `BANK_REPORTER_ROLE` and `CI_ROLE` ([marts_ownership.sql](../snowflake/marts_ownership.sql), Phase 3) |
| `RAW`, `STAGING` | ACCOUNTADMIN | the pipeline writes through grants |
| `OPS` | `BANK_PIPELINE_ROLE` | – |

## Why the pipeline owns the marts

Write-audit-publish ([ADR 0005](adr/0005-write-audit-publish-for-the-marts.md)) builds into
`MARTS_NEXT`, audits it, then publishes with `ALTER SCHEMA ... SWAP WITH`. Snowflake requires the
role running the swap to **own both schemas**; ownership of `MARTS` was handed to the pipeline role
for that in Phase 3 (`snowflake/marts_ownership.sql`, agreed 1 Nov 2026). Grants on a schema move
with it, which is why both carry the same reader grants.

## The risk

The owner can drop or replace the schemas and their tables. A mistake or a misuse of the pipeline
role could empty what Power BI reads.

What limits it today:
- **Recovery:** Time Travel on the database (1 day) and `UNDROP`; the marts can be rebuilt from
  `RAW` by `dbt build`; last week's marts survive a failed audit
  ([marts_publish.md](runbooks/marts_publish.md) swaps back).
- **Power BI** imports, so the report keeps the last good data until the next refresh.
- **Access to the role:** only the pipeline user `SVC_BANK_PIPELINE` (key pair in Vault since
  6 Nov 2026; network policy planned in PR 7).
- **Readers** (`BANK_REPORTER_ROLE`, `CI_ROLE`) can only read.

## Options

| Option | What changes | Cost |
|---|---|---|
| **A. Keep as is** (recommended) | nothing; this review is the record | none |
| B. A swap procedure owned by ACCOUNTADMIN (`EXECUTE AS OWNER`); the pipeline gets only `USAGE` on it, and the schemas move to ACCOUNTADMIN with managed access | the pipeline could no longer drop the schemas | new SQL, dbt and WAP changes and their tests; ownership of every mart table changes; a Phase 5-sized change |
| C. Longer Time Travel on `MARTS` (e.g. 7 days) | longer window to `UNDROP` | storage of 7 days of changes (small) |

**Recommendation:** A now; C is a cheap improvement if wanted (one `ALTER SCHEMA` by Martins); B
only if the pipeline role were ever shared or exposed beyond its one service user.

**Decision: A, keep as is** (Martins, 6 Nov 2026).
