# ADR 0006: Incremental, idempotent loads, and backfill through the weekly DAG

- **Status:** accepted (Phase 3, PRs 3–5, 31 Oct – 3 Nov 2026)
- **Findings:** D-02 (re-running a week must be a no-op)

## Context

Each layer was rebuilt in full every week. That hid duplicates and made re-runs and backfills hard
to reason about: is a second run harmless, or does it double something?

## Decision

1. **Bronze is append-only:** Auto Loader keeps every delivery, re-uploaded files included. It is
   compared on distinct keys, never on row counts.
2. **Silver merges** on its keys (Delta `MERGE`): bank-quarters on (cert, report_date), complaints on
   complaint_id; the newest load of a key wins. When the company → bank map changes, only the affected
   complaints are re-matched. Institutions, failures and gold stay full rebuilds (small, and
   history-dependent).
3. **dbt incremental facts:** `fct_bank_quarter` and `fct_complaints` merge only rows that are new or
   whose content changed (a `hash(*)` row hash), and delete rows no longer in the source.
   `full_refresh=true` rebuilds them.
4. **Snowflake RAW** keeps its full atomic swap per table, already idempotent.
5. **Backfill** is the weekly DAG with dates in its trigger form (or `scripts/backfill.py`): it
   re-downloads only that period, never moves the watermarks and never retrains
   ([backfill.md](../runbooks/backfill.md)).

## Evidence (dev, 2–3 Nov 2026)

The same week loaded twice, the same period backfilled twice, and incremental against full refresh
all left every table identical: row counts and checksums, bronze on distinct keys, lineage and
load-time columns left out (`scripts/acceptance_snapshot.py`).

## Alternatives considered

| Option | Why not |
|---|---|
| Keep full rebuilds | Simple, but a re-run's effect is invisible and a backfill redoes everything |
| A separate backfill DAG | Copies the pipeline's code; the weekly DAG with `max_active_runs=1` already rules out overlap |

## Consequences

Re-runs and backfills are safe and cheap. A backfill's extra bronze rows are reported, not alerted
on, and backfill runs are kept out of the volume history.
