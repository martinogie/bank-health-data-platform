# Validation checks (20)

Each check: what to look at, where the evidence is, and what passes. Rate each **Pass**,
**Pass with finding**, or **Fail** in the [report](report_template.md). "Card" = the
[model card](../model_card.md).

## Data and label

| # | Check | Evidence | Passes when |
|---|---|---|---|
| 1 | The label matches its definition: distress within 4 quarters = non-current loans > 3%, Tier 1 leverage < 5%, two consecutive net losses, or failure | `04_train_model.py` (label code), card "What it predicts" | the code implements exactly the card's definition, with quarters counted forward only |
| 2 | Banks already in distress are not scored as "entering" distress | `_scoring_rules.py`, card "Bands" (Already in distress) | already-distressed bank-quarters are excluded from training and shown as their own band |
| 3 | Exclusions are justified and counted: offices of foreign banks, banks under 3 years old | card "Rows removed from training", `03_gold.py` | each exclusion has a stated reason, row and bank counts, and the override list has evidence per entry |
| 4 | Missing is not zero: unreported capital ratios and CRE stay missing | dbt test `assert_unreported_capital_is_null`, card "What changed from v4" | no feature turns "not reported" into 0 |
| 5 | Outliers are handled deliberately (kept, with warnings audited) | card "Outliers kept", `OPS.DBT_WARNINGS_AUDIT` | the reasons to keep them hold, and the warned rows are visible per run |

## Leakage and split

| # | Check | Evidence | Passes when |
|---|---|---|---|
| 6 | Features use only information available at the quarter | `_features.py` | no feature reads a later quarter (changes are 1 and 4 quarters back) |
| 7 | Out-of-time split: train 2001–2019, test 2020–2024, test used once | `04_train_model.py` (PERIODS), MLflow run | model choice and band cut-offs never used the test years |
| 8 | Quarters whose 4-quarter outcome isn't complete are not trained on | `04_train_model.py` | the last 4 quarters are dropped from training |

## Performance

| # | Check | Evidence | Passes when |
|---|---|---|---|
| 9 | The published test results are the recorded ones | `scripts/validation_reproduce.py` (step 1) | every figure matches within the rounding tolerance |
| 10 | The model beats a simple threshold rule by a clear, stable margin | card "How much v5 beats a simple rule", `06_model_report.py` | the bootstrap interval of the difference (resampling banks) excludes 0 |
| 11 | Model choice was made on validation data, not test data | card "Model" (validation PR-AUC 0.343 vs 0.236), MLflow `validation_*` metrics | the champion is the best on 2017–2019 validation |
| 12 | Results hold by segment, and weak segments are disclosed | card "Results by segment", "Known limits" | every weak segment (CBLR, > $100B) is in the known limits |

## Calibration and bands

| # | Check | Evidence | Passes when |
|---|---|---|---|
| 13 | Calibration is measured and its error disclosed | card "Calibration" | over-prediction is stated with figures; probabilities are presented as rankings first |
| 14 | Band cut-offs come from out-of-sample predictions on training years only | card "Bands", `band_thresholds.json` in MLflow | walk-forward predictions 2003–2019, no test years |
| 15 | Scoring uses the version's own cut-offs | `_scoring_rules.BAND_TAGS`, version tags, open point VF-01 | the tags match the card; the run-metric discrepancy (VF-01) is explained or corrected |

## Reasons, monitoring, promotion

| # | Check | Evidence | Passes when |
|---|---|---|---|
| 16 | Reasons are faithful to the model and readable | card "Reasons", `m05_reason_examples.md` | SHAP contributions add up to the log-odds; unreported features are never cited; tier medians used |
| 17 | Drift and backtest monitoring have thresholds and reach a person | card "Monitoring", `07_monitor.py`, `OPS.MODEL_MONITORING` | PSI and capture thresholds are defined and alerts are on |
| 18 | A worse model can't become champion by mistake | card "Promotion rule", `_promotion.py` | promotion needs a person's `promote=true` and the gate on the same test rows |

## Operations and reproducibility

| # | Check | Evidence | Passes when |
|---|---|---|---|
| 19 | Training and scoring run in one pinned environment | card "Environment", `ENVIRONMENT_VERSION` in `ingestion/databricks_jobs.py` | the notebooks stop outside that environment; library versions are logged |
| 20 | Release checks protect the published list | card "Release checks", `scripts/sanity_check_top20.py` | the top-20 check runs before promotion and in every weekly run |
