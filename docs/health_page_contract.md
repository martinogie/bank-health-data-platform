# Health page: the data contract

The pipeline health page (to be built in the website phase) shows the **last 12 runs** of the
weekly pipeline. This document is its contract: what the data is, where it lives, what each
field means, and how fresh it is. **No front end is built in Phase 2.**

## Source

| | |
|---|---|
| Object | `BANK_HEALTH.OPS.HEALTH_RUNS_LAST_12` (a view; `BANK_HEALTH_DEV.OPS` has the same view for dev) |
| Built from | `OPS.PIPELINE_RUNS` (one row per run, written by the DAG's last task `record_run`) and `OPS.DBT_WARNINGS_AUDIT` |
| Defined in | `snowflake/setup_ops.sql` |
| Updated | at the end of every run (normally Monday about 06:30 CT); `CREDITS` becomes final at the next run |
| Rows | at most 12, the latest runs by `STARTED_AT`, successful and failed |
| Readable by | the pipeline role (owner) and `CI_ROLE` (read-only). Which role the website reads it with, and whether through a small export file instead of a live query, is decided in the website phase. |

Query for the page: `SELECT * FROM BANK_HEALTH.OPS.HEALTH_RUNS_LAST_12 ORDER BY STARTED_AT DESC;`

## Fields

| Field | Type | Meaning |
|---|---|---|
| `RUN_RANK` | number | 1 = the latest run |
| `RUN_ID` | text | Airflow run id, e.g. `scheduled__2026-11-02T12:00:00+00:00` |
| `ENVIRONMENT` | text | `prod` (or `dev` in the dev view) |
| `HOST_ID` | text | the machine that ran it: `oracle-vm` (or `pc` before cutover) |
| `STARTED_AT`, `ENDED_AT` | timestamp with time zone | when the run started and when `record_run` finished |
| `DURATION_MINUTES` | number | end − start, one decimal |
| `STATE` | text | `success` or `failed` |
| `FAILED_TASKS` | text | the task(s) that failed, comma-separated; empty on success |
| `BRONZE_ROWS`, `SILVER_ROWS`, `GOLD_ROWS` | number | Databricks row totals per layer (all tables in the layer; gold = `bank_quarter`) |
| `MARTS_ROWS` | number | Snowflake MARTS rows, all tables |
| `SCORED_BANKS` | number | rows in `MART_EARLY_WARNING` (= the watchlist) |
| `DBT_MODELS_OK`, `DBT_MODELS_ERROR` | number | dbt models built / failed |
| `DBT_TESTS_PASS`, `DBT_TESTS_WARN`, `DBT_TESTS_FAIL` | number | dbt test results (`FAIL` includes errors) |
| `WARNED_ROWS` | number | rows behind the warnings this run (e.g. CRE above 100% of loans), from `DBT_WARNINGS_AUDIT` (D-05) |
| `DQ_WARNINGS` | number | data-quality warnings inside the Databricks notebooks |
| `CREDITS` | number | Snowflake credits used, three decimals: the final figure if `CREDITS_ARE_FINAL`, else the estimate (C-02) |
| `CREDITS_ARE_FINAL` | boolean | false until the next run recomputes it from complete metering |
| `CREDITS_AVG_LAST_4` | number | moving average of `CREDITS` over this and the 3 runs before it: the cost trend line |
| `LATEST_FDIC_QUARTER` | date | newest quarter in the marts after this run |
| `SLO_MET` | boolean | succeeded by Monday 12:00 America/Chicago that week (the objective) |
| `HAD_VOLUME_ANOMALY` | boolean | `check_volumes` flagged a row-count drop or jump |
| `MODEL_NAME`, `MODEL_VERSION`, `MODEL_ALIAS` | text | the model that scored this run, e.g. `bank_health.ml.distress_model`, `5`, `champion` |

A value is **NULL** when its source didn't exist in that run. For example, a run that failed
before Databricks has no layer counts, and dev runs record no credits (no `MONITOR USAGE`). The
page should show "n/a" for those, never 0.

## What the page should show (guidance, not built here)

- A row per run: date, state (with the failed task), duration, scored banks, dbt
  pass/warn/fail, warned rows, credits, and an *on time* tick against the SLO.
- A credits trend from `CREDITS_AVG_LAST_4`, and the data freshness (`LATEST_FDIC_QUARTER`).
- A short note on the SLO: "data ready by Monday 12:00 CT".

## Changes

Adding a field is backwards compatible. Renaming or removing one is a breaking change: change
the view and the page together, in one pull request.
