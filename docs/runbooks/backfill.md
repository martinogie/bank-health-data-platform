# Runbook: redo a past period (backfill)

A backfill re-downloads one past period (some FDIC quarters, some days of CFPB complaints) and
runs it through every normal step of `bank_health_weekly`: data contracts, upload, bronze,
silver, gold, scoring, Snowflake RAW, dbt (write, audit, publish) and all the checks. Use it when a
source corrected old data (an amended call report, complaints updated later), or after fixing
a bug that affected a period.

It is the weekly DAG itself, started by hand with dates. There is no separate backfill DAG, so
a backfill runs exactly the code the weekly run runs.

## How to redo a period

**Not on a Monday in prod** (the weekly run's day; the run refuses). A backfill takes about as long
as a weekly run (about 30 minutes), and uses one of the day's Databricks runs.

1. **Open Airflow.** Prod: `ssh bank-health-vm` (keep that window open), then
   <http://localhost:8090>. Dev: <http://localhost:8081>.
2. **Dags → `bank_health_weekly`.** Its description reads *"Weekly bank data pipeline. To redo a
   past period, Trigger and fill in the backfill dates."*
3. **Click Trigger** (top right). The *Trigger Dag* dialog opens.
4. **Keep "Single Run" selected.** Don't choose *Backfill*: that's Airflow's own backfill, which
   only repeats plain weekly runs; the run stops at `check_backfill` and says so.
5. **Fill in the section "Redo a past period (backfill)"**: one range, or both.

   | Field | Example | Meaning |
   |---|---|---|
   | FDIC quarters: from / to | 2026-03-31 / 2026-06-30 | the quarters ending in this range (Mar 31, Jun 30, Sep 30, Dec 31) |
   | CFPB complaints: from / to | 2026-09-01 / 2026-09-30 | complaints received on these days |

   Leave *Other options* off. *Retrain the model* is refused in a backfill.
6. **Click Trigger.** A new run appears at the top of the run list.
7. **Watch `check_backfill`** (second task, about a minute in).
   - **Green:** its log says what will be redone, e.g. `Backfill: FDIC 2026-03-31..2026-06-30 (2
     quarters), CFPB 2026-09-01..2026-09-30; watermarks not moved, no retrain`.
   - **Red:** its log lists **every** reason at once (e.g. `to 2026-11-20 is in the future`).
     Nothing was downloaded. Fix the dates and trigger again.
8. **Wait for the message** in both channels: **"OK: bank_health_weekly backfill succeeded"**,
   with a "Backfill: …" line naming the period.
9. **Check afterwards (optional):** Admin → Variables: `fdic_last_repdte` and
   `cfpb_last_date_received` are what they were before. In Snowflake:
   `select RUN_ID, STATE, ROWS_DETAIL:backfill from BANK_HEALTH.OPS.PIPELINE_RUNS order by STARTED_AT desc limit 1;`

**From the command line** (same run, same checks), in the project folder:

```bash
python3 scripts/backfill.py --fdic 2026-03-31:2026-06-30 --cfpb 2026-09-01:2026-09-30 --dry-run   # VM: check, show the command
python3 scripts/backfill.py --fdic 2026-03-31:2026-06-30 --cfpb 2026-09-01:2026-09-30             # VM: trigger
.venv\Scripts\python scripts/backfill.py --cfpb 2026-09-01:2026-09-30                             # PC (dev)
```

One date means one quarter or one day (`--fdic 2026-06-30`). The script checks the dates (both
ends, from before to, nothing in the future) and then triggers through
`docker compose exec airflow-scheduler airflow dags trigger`; the run checks the rest.

## The rules, and why

Checked by `check_backfill` (`ingestion/backfill.py`) before anything is downloaded:

| Rule | Why |
|---|---|
| Both dates of a range, from ≤ to, nothing after today | a typo shouldn't start a 30-minute run |
| FDIC: at least one quarter-end in the range; not before 2000-03-31 | quarters are what the FDIC publishes; the history starts there |
| FDIC: not newer than `fdic_last_repdte`; CFPB: not after `cfpb_last_date_received` | new data comes with the weekly run, which also moves the watermarks |
| CFPB: not more than 3 years back | the pipeline keeps 3 years of complaints |
| No `retrain_model` | a backfill re-scores with `@champion`; retraining is a separate decision |
| Prod: not on a Monday | Monday belongs to the weekly run and its 12:00 deadline |
| One run at a time (`max_active_runs=1`) | a backfill never runs alongside the weekly run; if one is going, the other waits |
| Prod: the one-scheduler guard (`check_environment`) | as on every run: only the machine named in `OPS.PROD_SCHEDULER` can run prod |

## Watermarks, re-runs and backfills

Two Airflow Variables say how far the weekly run has got:

- `fdic_last_repdte`: the newest FDIC quarter loaded. Each weekly run downloads every newer
  quarter, **plus the last 2 again** (banks amend call reports).
- `cfpb_last_date_received`: the last day of complaints loaded (yesterday, at run time). Each
  weekly run downloads from **30 days before it** to yesterday (complaints get updated).

`save_watermarks` moves them forward only after the files are safely in Databricks.

| What you do | Watermarks | What is downloaded | Result |
|---|---|---|---|
| Weekly run | move forward | new data + the overlap (2 quarters, 30 days) | new rows; re-downloaded rows replace their older copies |
| Re-run a weekly run (clear it, or trigger again) | already moved: stay, or move to the same values | the overlap again | silver onward unchanged, if the source didn't change |
| A run fails before `save_watermarks` | not moved | the next run downloads the same windows | nothing lost |
| **Backfill** | **read as limits, never written** | only the chosen period | that period refreshed; the next weekly run downloads exactly what it would have anyway |

Why a backfill never moves them: moving one **back** would make the next weekly run download
everything since then again (slow, and a quota risk); moving one **forward** would skip data the
weekly run hasn't fetched yet.

How a re-download stays harmless, layer by layer: bronze appends (it keeps every delivery);
silver keeps the newest load of each bank-quarter and complaint (`_silver_merge`); gold and the
risk scores are rebuilt; Snowflake RAW is replaced table by table; `fct_bank_quarter` and
`fct_complaints` merge only rows whose content changed (`_row_hash`).

## Checks during a backfill

- **Volume check:** a backfill adds far more rows to bronze than a weekly run, by design. In a
  backfill the **rows-added** check only **reports** (shown in the `check_volumes` log); the
  silver, gold, marts and scored-bank checks still apply and can fail the run. Backfill runs
  are left out of the history the weekly runs are judged against (`ROWS_DETAIL:backfill` is set),
  so a backfill never shifts the "usual" rows added.
- **dbt tests:** unchanged. The same models and tests run; write, audit, publish swaps the marts in
  only if every critical test passed.
- **Freshness, top-20 sanity check, end-to-end checks:** unchanged.
- **SLO:** not judged (a manual run); the success message says "n/a (manual run)".
- **Watchlist:** prod refreshes `watchlist.json` as on a weekly run (same scored banks).

## Acceptance (D-02, dev)

The plan's acceptance runs: the same week twice, the same quarter backfilled twice, and
incremental against full refresh, all leaving identical row counts and checksums per table
(bronze on distinct keys). Snapshots: `scripts/acceptance_snapshot.py take <name>` after a run,
`compare <a> <b>` between two (`ingestion/checksums.py`; the lineage and load-time columns
`_source_file`, `_ingested_at`, `_file_modified_at`, `_LOADED_AT` and `SCORED_AT` are left out,
since they say where and when a row was loaded, not what it says).

Results: *filled in after the dev runs.*
