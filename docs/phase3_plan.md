# Phase 3 plan: production-grade data and model operations

Agreed on 29 Oct 2026. Source: the critical review (Phase 3, findings M-03, M-07, D-01, D-02, E-01,
M-05, M-08, M-09). Status of each PR is updated here as it merges.

**Scope.** Phase 3 does not retrain or change the v5 model. It changes data handling, reason codes
and the model operations around it. Out of scope: M-06 calibration (v6.5) and M-10 hazard model
(v6.6), see [ROADMAP.md](ROADMAP.md); D-04 bank history moves to the v7 preparation (7.0).

**Method.** One branch and PR at a time from `main`. Each PR is tested in dev, then CI must be
green, then Martins approves the merge and the deploy. No merges or deploys on Mondays. Prod is
the Oracle VM, and changes reach it only through a merge and an approved deploy.

**Budgets.** The dev Snowflake cap is 2 credits a month. Any dev run expected above 0.1 credits is
announced first. Databricks Free Edition has a daily quota: at most 2 dev pipeline runs a day.

## Order and what each PR touches

| # | PR | Findings | Touches prod | Dev Snowflake credits (estimate) |
|---|---|---|---|---|
| 0 | Docs: this plan, roadmap, backlog, test classes approved | D-04 move | No (docs) | 0 |
| 1 | Model report: baselines and segment results | M-03, M-07 | No: an analysis notebook outside the job | 0 |
| 2 | Data contracts before bronze | D-01 | Yes: a new check in the weekly run | ~0.08 |
| 3 | Silver MERGE and table checksums | D-02 | Yes: silver write mode | ~0.08 |
| 4 | dbt incremental facts and write, audit, publish | D-02 | Yes: how the marts are built | ~0.3 |
| 5 | Backfill DAG and acceptance runs | D-02 | Yes: a new DAG, deployed paused | ~0.4 |
| 6 | Complaint matching quality | E-01 | Analysis: no. Reviewed overrides: yes | ~0.08 |
| 7 | SHAP reasons | M-05 | Yes: reason texts | ~0.08 |
| 8 | Model monitoring | M-08 | Yes: a new task, table and alert | ~0.1 |
| 9 | Gated promotion | M-09 | Only when a retrain runs (v6) | 0 |
| 10 | Model card and README | all | Docs | 0 |

## Decisions (29 Oct 2026)

1. PR 1 and PR 6 may **read** prod tables (`ml.model_frame`, silver complaints, the company map)
   from dev notebooks. They write nothing to prod.
2. Snowflake is a paid Standard edition account; no trial deadline.
3. M-03 reports the existing strict-label challenger only; no new training.
4. M-09 tolerances: a challenger may be at most 0.005 PR-AUC and 1.0 point of top-10% capture
   below the champion, on the same test rows. M-08 "sharp drop": realised top-10% capture more
   than 10 points below the test result (52.6%), judged only for cohorts with at least 30
   positives.
5. Write, audit, publish is part of PR 4. A cloned schema does not keep grants made on the schema
   itself, so the swap re-applies the schema-level grants (USAGE and future grants for
   `BANK_REPORTER_ROLE` and `CI_ROLE`), and the acceptance runs prove both roles can still read
   `MARTS` after a swap.
6. The dbt test classes (92 critical, 4 warn) are approved; the publishing gap closes in PR 4.
7. M-08 backtest: only quarters from 2020 on count. Rescored 2001–2019 quarters are in-sample:
   marked as such, kept out of the alert and the headline numbers.
8. M-08 PSI: each quarter is compared with the previous 4 quarters (the alert is mainly on this),
   and with 2001–2019 training (reported, no alert). Both are shown in dev before the alert is on.

## PR 1: baselines (M-03) and segments (M-07)

- A notebook `06_model_report.py`, not part of the weekly job. It reads the v5 champion and the
  prod test rows read-only, and writes to the dev catalog and a dev MLflow run.
- **Rule baseline:** rank by distance to the nearest threshold (non-current loans 3%, Tier 1 5%).
  Two scalings are tried and one chosen on 2001–2019 only, then reported on 2020–2024.
- **Metrics:** PR-AUC and top-10% capture for v5, the rule and logistic regression, with 95%
  intervals from a bootstrap that resamples banks (a bank's quarters are not independent).
- **Strict challenger:** existing numbers and the trade-off. Note: the v5 challenger label is
  failure, Tier 1 < 5% or non-current loans > 3% (it drops the two-losses rule); the review
  suggested failure or Tier 1 only.
- **Segments:** ROC-AUC, PR-AUC and capture by asset-size tier, and by Census region if the sample
  allows, with rows and positives; under 30 positives = too small to judge. CBLR vs non-CBLR.
- **Stops:** Martins reviews the tables before they go into the model card.

## PR 2: data contracts (D-01)

- One contract per source (FDIC financials, institutions, failures; CFPB complaints): fields,
  types, allowed nulls, code lists. Checked on each download before `upload_raw`, so a bad file
  never reaches bronze.
- **Fails** (FAILED alert naming the field): a removed or renamed field, a type change, nulls
  where none are allowed. **Warns:** a new field, a new code value.
- Each run's observed schema is stored as JSON next to the raw files, and diffed with the last.
- Unit tests: renamed and retyped fields fail; a new field and a new code value warn.
- First prod run in warn-only mode, then switched to enforce after one clean run.
- **Stops:** contract review before coding; the switch to enforce.

## PRs 3–5: idempotent loads and backfill (D-02)

- **Silver:** Delta MERGE on (cert, report_date) and on complaint_id, newest load wins. If the
  company map changes, only the affected complaints are re-matched. Institutions, failures and
  gold stay full rebuilds (snapshots; lags, year-to-date to quarterly and the 4-quarter-ahead
  label depend on history).
- **Bronze** is append-only by design, so its row counts grow on re-runs. It is compared on
  distinct keys; every later table must be identical.
- **Snowflake RAW** keeps its full atomic swap, which is already idempotent.
- **dbt:** `fct_complaints` incremental on complaint_id, merging rows whose content changed (old
  complaints change response or matched bank, so a date look-back alone would miss them).
  `fct_bank_quarter` incremental on (cert, report_date) with an 8-quarter look-back. `dim_bank`,
  the scorecard and the early-warning mart stay full tables. `--full-refresh` stays available.
- **Write, audit, publish:** clone `MARTS` to `MARTS_NEXT`, build and test there, swap only if
  every critical test passed, then re-apply the schema-level grants.
- **Backfill:** a DAG `bank_health_backfill` for a quarter or date range, through every step. It
  never moves a watermark backwards or past the weekly run, never runs alongside the weekly run,
  never retrains, uses the one-scheduler guard in prod, and is deployed paused.
- **Acceptance in dev:** the same week twice, the same quarter backfilled twice, and incremental
  vs full refresh all leave identical row counts and checksums per table; the reporter role and
  `CI_ROLE` read `MARTS` after a swap.
- **Stops:** checksum design; the effect on dbt tests and on the `PIPELINE_RUNS` volume checks
  before merging; grant scripts for Martins to run; one prod full-refresh seeding run.

## PR 6: complaint matching quality (E-01)

- A random sample (fixed seed) of 100 matched and 100 unmatched companies, each with a proposed
  label and evidence, in `docs/investigations/e01_match_labels.csv` with a `confirmed_by_martins`
  column. No figure is published before Martins confirms the labels.
- Precision and recall by company and by complaint volume, with Wilson 95% intervals; the
  unmatched volume by type (debt collector, fintech, credit union, other non-bank, true miss);
  how complaints split under holding companies with several banks.
- Fuzzy matching only proposes candidates; only rows Martins approves are applied. Before/after.
- **Stops:** label review; candidate review; the before/after numbers before merging.

## PR 7: SHAP reasons (M-05)

- First: a shap version that works in Databricks serverless environment 2 (Python 3.11,
  numpy 1.23, scikit-learn 1.5.2) on the v5 model, including rows with missing values; pinned.
  If none fits, stop and report.
- Plain-English reasons vs the asset-tier peer median; a missing value is never cited; the median
  method kept as the fallback, shown in a new `reason_method` column.
- Reasons for 10 known 2009–2010 failures (in-sample for v5; marked) and today's top 20, old vs
  new.
- `reason_1..3` keep their names and types; the only addition is `reason_method`.
- **Stops:** the shap version; review of the examples before merging.

## PR 8: monitoring (M-08)

- PSI per feature and for the score, each run: vs the previous 4 quarters (alert) and vs
  2001–2019 training (reported).
- Backtest once outcomes are known: realised PR-AUC and top-10% capture per quarter, 2020 on. A
  new append-only `ml.risk_score_history` keeps past scores; earlier quarters are filled by
  rescoring with v5, and 2001–2019 is marked in-sample.
- `OPS.MODEL_MONITORING` in prod and dev from a script Martins reviews and runs; alerts through
  the Phase 2 alerts; a monitoring block in the health data.
- **Stops:** the SQL script; both PSI comparisons shown in dev before the alert goes on.

## PR 9: gated promotion (M-09)

- A tested rule (pass, fail, tie): promote only if not worse than the champion beyond the
  tolerances, on the same test rows (checked by a hash of the row keys). A person's
  `promote=true` is still required. The decision is recorded in MLflow. Runs only on a retrain.

## PR 10: documentation

- Model card: baselines, segments, SHAP reasons, monitoring, the promotion rule.
- README data section: contracts, idempotent loading and backfill, complaint-matching quality.
