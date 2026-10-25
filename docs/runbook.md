# Runbook

Short procedures for running and releasing the Bank Health pipeline. Commands run from the
project folder on Windows; secrets come from `.env` and are never printed.

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

Rolling back = moving `@champion` back to the previous version and re-scoring.

## Databricks environment

Training and scoring must run in the same serverless environment version, or a saved model may
not load (a model pickled with numpy 2 does not load under numpy 1). The version is recorded in
`docs/model_card.md`; `04_train_model` and `05_score` stop if they run in a different one.
