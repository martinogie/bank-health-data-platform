# Runbook

Short procedures for running and releasing the Bank Health pipeline. Commands run from the
production folder on Windows, whose `.env` has `BANK_HEALTH_ENV=prod` (in a dev checkout the
same commands run against `BANK_HEALTH_DEV` / `bank_health_dev` and refuse to promote or
publish; see [CONTRIBUTING.md](../CONTRIBUTING.md)). Secrets come from `.env` and are never printed.

## Review the top 20 before release

Do this before promoting a model and whenever the band counts or the model change noticeably.
The weekly DAG runs the same check automatically (`sanity_check_top20`, after scoring and before
dbt publishes the marts) and fails the run if it finds a problem.

1. Print the 20 riskiest scored banks and run the automatic checks:
   - production scores in Snowflake: `.venv\Scripts\python scripts/sanity_check_top20.py`
   - a candidate model before promotion (scored into `ml.risk_scores_candidate`):
     `.venv\Scripts\python scripts/sanity_check_top20.py --databricks ml.risk_scores_candidate`
2. The script fails on a new bank (under 3 years old), an office of a foreign bank, a 0% or
   missing Tier 1 leverage ratio, or a reason quoting an unusable capital ratio.
3. Then read the list yourself:
   - Do the names make sense? Look up two or three on FDIC BankFind and check the figures behind
     the top reason.
   - Is any bank there only because of a data artefact (a ratio of exactly 0, a missing quarter,
     a merger)? If so, fix the data rule, not the model.
4. Check `gold.capital_zero_review` (and the `REVIEW:` lines in the 03_gold run log). Each bank
   listed has both capital ratios at 0 in 4 or more quarters but no foreign-office flag. If it is
   a foreign-bank office, add it to `FOREIGN_OFFICE_OVERRIDES` in `03_gold.py` **and**
   `foreign_office_overrides` in `dbt/dbt_project.yml`, with the evidence in a comment.
5. Record the date, model version and outcome in the pull request or release notes.

## Promote a model

Training (`04_train_model`) registers each new version as `@candidate` with its band cut-offs
as version tags. Scoring uses `@champion`. To promote after review:

1. Score with the candidate into a separate table (production scores are not touched):
   `.venv\Scripts\python scripts/databricks_run.py 05_score --param=model_alias=candidate`
2. Review the top 20 (above), the band counts and the test results in MLflow.
3. Move the alias in Unity Catalog (Catalog Explorer → `bank_health.ml.distress_model` →
   version → Aliases), or set `champion` with the MLflow client. Only after approval.
4. Run the pipeline (or `05_score` and the Snowflake load) so production scores use the new
   champion.

## Roll back to v4

Use this if v5 (or a later version) misbehaves after promotion. v4 was trained in serverless
**environment 4** (numpy 2) and does not load in environment 2, and it needs the
total risk-based capital feature that v5 code no longer builds. A rollback is therefore a code
rollback plus a model rollback, not just an alias move.

1. **Pause the DAG** (Airflow UI → `bank_health_weekly` → pause), so no run scores with a mix.
2. **Code:** on a branch from `main`, revert the v5 merge commit
   (`git revert -m 1 <merge commit>`). This restores v4's features (with total risk-based
   capital), percentile bands and scoring. The revert also brings back the old gold rules, so
   CBLR banks and foreign offices show 0% ratios again (the reason v5 exists).
3. **Environment pin:** the revert also removes the pin (it came with v5), and the job would then
   run in the workspace default. Don't rely on that: keep the pin and point it at v4's environment.
   - `git checkout <v5 merge commit> -- ingestion/databricks_jobs.py scripts/databricks_run.py ingestion/tests/test_databricks_jobs.py`
   - set `ENVIRONMENT_VERSION = "4"` in `ingestion/databricks_jobs.py` and the expected value in
     `test_every_task_runs_in_the_pinned_environment`;
   - run `pytest`, commit with the revert.
   The v4 notebooks have no environment guard, so nothing else changes.
4. **Alias:** move `@champion` back to version 4 (Catalog Explorer → `bank_health.ml.distress_model`
   → version 4 → Aliases). Leave v5 registered; it keeps its `@candidate` alias for later.
5. **Re-run** gold and scoring with the reverted code: merge the revert, then trigger the DAG with
   `{"retrain_model": false}`. Gold, scores, Snowflake RAW, dbt marts and the watchlist file are all
   rebuilt by that run.
6. **Check:** `scripts/sanity_check_top20.py` (expect the v4 failures it was written to catch:
   0% capital ratios in the top 20), Power BI refresh, and record the rollback and its reason in
   the release notes.

To return to v5 later, revert the revert, set the pin back to `"2"`, and move `@champion` to 5.

## Databricks environment

Training and scoring must run in the same serverless environment version, or a saved model may
not load (a model pickled with numpy 2 does not load under numpy 1). The version is recorded in
`docs/model_card.md`; `04_train_model` and `05_score` stop if they run in a different one.
