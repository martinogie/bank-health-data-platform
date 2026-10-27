# Runbook: Databricks Free Edition quota errors

Databricks Free Edition gives a **daily compute quota** on serverless compute. When a run uses
it up, Databricks stops the job for the rest of the day, and `run_databricks_job` fails. The
limits, and why we live with them: [ADR 0003](../adr/0003-databricks-free-edition-limits.md).

## How you'll know

The failure alert says so in its title and body:

```
[PROD] FAILED: bank_health_weekly / run_databricks_job: Databricks Free Edition daily quota reached
...
Databricks Free Edition daily quota reached. See docs/runbooks/quota_errors.md.
```

The check is `is_databricks_quota_error()` in `ingestion/alerts.py`. Databricks doesn't document
the exact wording, so it matches several phrasings ("quota", "usage limit", "daily limit",
"resource exhausted"). If a real quota stop ever arrives *without* that line, copy its error
text into a test in `ingestion/tests/test_alerts.py` and extend the pattern.

## What to do

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

## Avoiding it

- **Dev and prod share one daily quota.** Never run the dev pipeline on a Monday before the prod
  run has finished; keep dev runs to a small slice (CONTRIBUTING.md, "Run the dev pipeline").
- **Retraining** (`04_train_model`, about once a quarter) uses the most compute. Don't combine a
  retrain with other manual Databricks work on the same day.
- The run log keeps a history of run durations (`OPS.PIPELINE_RUNS`). A steady climb in run time
  is the early warning.
