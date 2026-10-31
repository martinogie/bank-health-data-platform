# Runbook: publishing the marts (write, audit, publish)

Since Phase 3 (PR 4) the weekly run never rebuilds the published marts in place:

1. **`prepare_marts`** clones `MARTS` into `MARTS_NEXT` (zero-copy, seconds, no credits for the
   copy). The incremental marts (`FCT_BANK_QUARTER`, `FCT_COMPLAINTS`) arrive already filled.
2. **`dbt_build`** builds and tests every mart **in `MARTS_NEXT`**. The incremental marts merge only
   rows that are new or changed (`_ROW_HASH`) and delete rows that left the source.
3. **`publish_marts`** runs only if every dbt model and critical test passed. It grants the readers
   on `MARTS_NEXT` (prod: `BANK_REPORTER_ROLE` for Power BI, `CI_ROLE` for dbt slim CI), then swaps
   `MARTS_NEXT` with `MARTS` in one statement. Readers never lose access.
4. **`dbt_docs_generate`** runs after the swap, so the docs and the manifest slim CI defers to name
   `MARTS`. `publish_dbt_manifest` refuses a manifest that mentions `MARTS_NEXT`.

After a successful run, `MARTS_NEXT` holds **last week's** marts until the next run replaces it.

## When a critical test fails

`publish_marts` doesn't run, so **`MARTS` still holds last week's marts**: Power BI, the watchlist
and CI keep working on the last good data. The run fails and alerts as usual. `MARTS_NEXT` holds
the failed build: inspect it (`select ... from BANK_HEALTH.MARTS_NEXT.<table>`), fix the cause,
then clear `prepare_marts` with *downstream* selected (it recreates `MARTS_NEXT`).

## Roll back a bad publish (within the week)

Swap again, as the pipeline role (or ACCOUNTADMIN):

```sql
USE ROLE BANK_PIPELINE_ROLE;
ALTER SCHEMA BANK_HEALTH.MARTS SWAP WITH BANK_HEALTH.MARTS_NEXT;  -- last week's marts are live again
```

Last week's schema still has its grants, so readers keep access. Don't run the pipeline again
until the cause is fixed: the next `prepare_marts` replaces `MARTS_NEXT` (the rolled-back copy).

## A full rebuild of the incremental marts

Trigger the DAG with `{"full_refresh": true}`. `prepare_marts` drops the two incremental marts from
the clone, so dbt rebuilds them from scratch; everything else is as usual. It also happens on its
own for a mart that has no `_ROW_HASH` column yet (the first run after PR 4).

## Ownership

Swapping needs ownership of both schemas. `BANK_PIPELINE_ROLE` owns `MARTS`
([snowflake/marts_ownership.sql](../../snowflake/marts_ownership.sql), run once) and creates
`MARTS_NEXT` itself.

## Who can read the marts

- **Each publish** grants `USAGE` on `MARTS_NEXT` and `SELECT` on every table and view in it to
  the readers, then swaps. Every mart exists by then, so each one is readable the moment it goes
  live.
- **Database-level future grants** (`SELECT` on future tables and views in `BANK_HEALTH`) for
  `BANK_REPORTER_ROLE` and `CI_ROLE` are the safety net for anything created later. They are set
  once, as ACCOUNTADMIN ([snowflake/marts_ownership.sql](../../snowflake/marts_ownership.sql),
  step 3; `snowflake/setup_dev.sql` for `CI_ROLE`).
- **Schema-level future grants are not re-applied** at a swap: Snowflake requires the account-wide
  `MANAGE GRANTS` privilege for them, even for the schema's owner, and the pipeline role isn't
  given it. The ones `MARTS` had before Phase 3 disappear at its first swap; the database-level
  grants take over.
