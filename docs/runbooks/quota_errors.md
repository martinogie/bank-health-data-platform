# Runbook: quota errors (Databricks daily quota, Snowflake credit caps)

Two different limits can stop a run. The failure alert names which one, and links to its section:

| Alert says | What ran out | Section |
|---|---|---|
| `Databricks Free Edition daily quota reached` | Databricks serverless compute, for the day | [below](#databricks-free-edition-daily-quota) |
| `Snowflake credit cap reached (<monitor>)` | a Snowflake resource monitor's monthly credits | [below](#snowflake-credit-cap-reached) |

The check is `quota_reason()` in `ingestion/alerts.py`. Snowflake's error (code `090073`) also
contains the word "quota", so it is recognised first and never reported as a Databricks stop.

## Databricks Free Edition daily quota

Databricks Free Edition gives a **daily compute quota** on serverless compute. When a run uses
it up, Databricks stops the job for the rest of the day, and `run_databricks_job` fails. The
limits, and why we live with them: [ADR 0003](../adr/0003-databricks-free-edition-limits.md).

### How you'll know

The failure alert says so in its title and body:

```
[PROD] FAILED: bank_health_weekly / run_databricks_job: Databricks Free Edition daily quota reached
...
Databricks Free Edition daily quota reached. See docs/runbooks/quota_errors.md#databricks-free-edition-daily-quota.
```

The check is `is_databricks_quota_error()` in `ingestion/alerts.py`. Databricks doesn't document
the exact wording, so it matches several phrasings ("quota", "usage limit", "daily limit",
"resource exhausted"). If a real quota stop ever arrives *without* that line, copy its error
text into a test in `ingestion/tests/test_alerts.py` and extend the pattern.

### What to do

1. **Don't retry today.** Every retry fails until the quota resets, and Airflow has already used
   its one retry for this task.
2. **Find where the job stopped.** Databricks → *Jobs & Pipelines* → `bank_health_pipeline` →
   the failed run. Tasks run in order: `bronze → silver → gold → train_model → score`. Note
   which ones succeeded.
3. **The next day (after the quota resets), resume from the failed notebook.** In that same
   Databricks run, click **Repair run**: Databricks re-runs only the failed task and the ones
   after it. The finished notebooks aren't run again, which saves quota. Each notebook is safe to
   re-run on its own:
   - bronze: Auto Loader checkpoints skip files already ingested;
   - silver, gold and scores: overwrite their tables;
   - train_model: skips unless retraining was asked for.
4. **Tell Airflow the job is done.** When the repaired Databricks run has succeeded, open the
   Airflow run → `run_databricks_job` → **Mark as → success**. Then **clear** `load_snowflake`
   with *downstream* selected: Airflow runs the Snowflake load, the release check, dbt, the
   checks, the watchlist and `record_run` from there.
   - Alternative, simpler but costing more quota: just **clear** `run_databricks_job` (with
     downstream). It starts a new job run from bronze.
5. **Check the outcome:** the *OK* message in Slack and Discord, and the new `OPS.PIPELINE_RUNS`
   row. If this happens on a Monday after 12:00 CT, the late-run alert has already fired; that's
   expected.

### Avoiding it

- **Dev and prod share one daily quota.** Never run the dev pipeline on a Monday before the prod
  run has finished; keep dev runs to a small slice (CONTRIBUTING.md, "Run the dev pipeline").
- **Retraining** (`04_train_model`, about once a quarter) uses the most compute. Don't combine a
  retrain with other manual Databricks work on the same day.
- The run log keeps a history of run durations (`OPS.PIPELINE_RUNS`). A steady climb in run time
  is the early warning.

## Snowflake credit cap reached

Snowflake **resource monitors** cap credits per month and suspend a warehouse at 100%:

| Monitor | Watches | Cap (credits / month) | Used by |
|---|---|---|---|
| `BANK_DEV_WH_MONITOR` | `BANK_DEV_WH` | 2 | dev pipeline runs, dbt slim CI, the GitHub late-run watchdog |
| `ACCOUNT_MONTHLY_CAP` | the whole account | 5 | everything, including prod's Monday runs on `BANK_WH` |

### How you'll know

```
[DEV] FAILED: bank_health_weekly / load_snowflake: Snowflake credit cap reached (BANK_DEV_WH_MONITOR)
...
Snowflake credit cap reached (BANK_DEV_WH_MONITOR). See docs/runbooks/quota_errors.md#snowflake-credit-cap-reached.
Error:
ProgrammingError: 090073 (22000): Warehouse 'BANK_DEV_WH' cannot be resumed because resource monitor 'BANK_DEV_WH_MONITOR' has exceeded its quota.
```

A dbt slim CI job or the watchdog fails with the same `090073` error in its GitHub log.

### What to do

1. **Don't retry.** Every query on that warehouse fails until the monitor resets or the cap is
   raised. Stop dev runs and pause anything else that uses the warehouse.
2. **See how much is used and when it resets** (as ACCOUNTADMIN, read-only):
   `SHOW RESOURCE MONITORS;` gives `used_credits`, `remaining_credits` and the reset time
   (`end_time` / the next interval, counted from the monitor's `start_time`).
3. **See what spent it:** `snowflake/credit_usage.sql` (read-only; credits per day and the main
   query sources).
4. **Decide:** wait for the reset, or raise the cap. Raising a cap, and any change to a monitor or
   a warehouse, is Martins' decision, made as ACCOUNTADMIN. If `ACCOUNT_MONTHLY_CAP` is the one
   that ran out, **prod is stopped too**: deal with that first.
5. **Resume:** once the warehouse can run again, clear the failed task with *downstream* selected.
   Nothing before it needs to run again (the Databricks exports are still there).

### Avoiding it

- Dev runs use the small dev slice and are announced when expected above 0.1 credits.
- dbt slim CI builds only what a change touches, and runs only when dbt files change.
- Check `snowflake/credit_usage.sql` before a series of dev runs.
