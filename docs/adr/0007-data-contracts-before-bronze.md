# ADR 0007: Data contracts checked before anything reaches bronze

- **Status:** accepted (Phase 3, PR 2, 30 Oct 2026); enforce mode in prod from Phase 4
- **Findings:** D-01 (no checks that the source APIs still look the way the code expects)

## Context

The FDIC and CFPB APIs can rename, retype or drop a field without notice. Bronze keeps values as
text, so a change would surface weeks later as wrong ratios or empty columns.

## Decision

1. **One contract per source** (`ingestion/contracts/*.yml`): fields, types, allowed nulls and code
   lists, each field with a level (key, measure, nullable, info).
2. **`check_contracts` runs on every fresh file, before `upload_raw`.** A removed or renamed field, a
   type change, or nulls where none are allowed **fail** the run, with an alert naming the field; a
   new field or a new code value **warns**.
3. **Each file's observed schema** is saved next to the raw files and compared with the last.
4. **Rollout:** prod ran in `warn` mode first (report what would fail; fail-open, so a bug in the
   checker itself never stops a run), then switched to `enforce` once the runs were clean
   (Phase 4). Warning and would-fail counts go to `OPS.PIPELINE_RUNS`.

## Alternatives considered

| Option | Why not |
|---|---|
| dbt source tests only | They run after loading: the bad file is already in bronze and silver |
| Great Expectations | A heavy dependency for four small contracts |

## Consequences

A breaking API change stops the run before any layer changes, with an alert naming the field. The
fix is a reviewed PR to the contract or the code.
