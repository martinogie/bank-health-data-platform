# ADR 0005: Write, audit, publish for the marts

- **Status:** accepted (Phase 3, PR 4, 1 Nov 2026)
- **Findings:** D-02 (a broken model must never reach the report)

## Context

dbt rebuilt the marts in place. A model that built but failed a critical test had already replaced
the published table: Power BI and the watchlist could read wrong numbers until someone noticed.

## Decision

1. `prepare_marts` clones `MARTS` into `MARTS_NEXT` (zero-copy).
2. dbt builds and tests every mart in `MARTS_NEXT` (dbt var `marts_schema`).
3. `publish_marts` runs only if every model and critical test passed: it grants the readers on
   `MARTS_NEXT` (prod: `BANK_REPORTER_ROLE` for Power BI, `CI_ROLE` for slim CI), then swaps the two
   schemas in one statement (`ALTER SCHEMA ... SWAP WITH`).
4. Last week's marts stay in `MARTS_NEXT` until the next run: rollback is one more swap
   ([marts_publish.md](../runbooks/marts_publish.md)).
5. dbt's manifest, used by slim CI, must name `MARTS`, never `MARTS_NEXT`; the run refuses it
   otherwise.

## Alternatives considered

| Option | Why not |
|---|---|
| Build in place and rely on tests | The tests run after the table is replaced: too late |
| Views pointing at a versioned schema (blue/green) | More objects, and every reader must go through the views |
| A zero-copy clone of the whole database | Clones far more than the marts; harder to reason about |

## Consequences

- Readers never see a half-built or failing mart; a failed audit leaves last week's marts in place.
- The swap needs the pipeline role to own both schemas. Grants made on a schema don't survive a
  clone, so `publish_marts` re-applies them before the swap; database-level future grants cover new
  tables. Whether the pipeline role should own `MARTS` is reviewed in Phase 4 (PR 10).
- `MARTS` and `MARTS_NEXT` change every week, so Terraform does not manage them (ADR 0010).
