# End-to-end run report: 2026-10-20

Airflow DAG `bank_health_weekly`, run `scheduled__2026-10-19T11:00:00+00:00`, first full-history
run. Final state: **success** (49 tasks). All figures below were measured after the run.

## How the run went
1. `download_cfpb` failed once: the CFPB CSV export refuses very large requests (January 2025
   = 123,553 complaints). Fix: windows over 75,000 rows are split. The retry reused the 15
   months already on disk.
2. `run_databricks_job` failed in `01_bronze`'s summary line (a `None` row count) after the data
   had been written. One-line fix; the run was resumed from that task.
3. `05_score` couldn't load model v3: scheduled jobs run Python 3.12, v3 was saved with
   scikit-learn 1.3.0 on Python 3.11. Fix: scikit-learn pinned to 1.5.2 in 04/05.
   The job's `train_model` + `score` tasks were run once with `retrain=true` (bronze/silver/gold
   had already succeeded), registering **v4** as `@champion`. The Airflow task was then marked
   successful with a note, and Airflow ran `load_snowflake`, dbt, docs and `verify_pipeline`.

## Rows per table
| Layer | Table | Rows |
|---|---|---|
| Databricks bronze | fdic_financials / fdic_institutions / fdic_failures | 768,654 / 83,505 / 12,351 |
| | cfpb_complaints | 2,035,852 |
| Databricks silver | fdic_financials / fdic_institutions / fdic_failures | 751,322 / 27,835 / 4,117 |
| | cfpb_complaints / company_bank_map | 1,969,948 / 5,387 |
| Databricks gold / ml | bank_quarter / model_frame / risk_scores | 743,802 / 743,802 / 4,257 |
| Snowflake RAW | FDIC_FINANCIALS / FDIC_INSTITUTIONS / FDIC_FAILURES | 751,322 / 27,835 / 4,117 |
| | CFPB_COMPLAINTS / ML_RISK_SCORES | 1,969,948 / 4,257 |
| Snowflake MARTS | DIM_BANK / FCT_BANK_QUARTER / MART_BANK_SCORECARD | 27,835 / 743,802 / 743,802 |
| | FCT_COMPLAINTS / MART_EARLY_WARNING | 1,969,948 / 4,257 |

Bronze is larger than silver because re-uploaded files are ingested again; silver keeps the
newest load per key.

**Scope of complaints:** CFPB complaints received 2023-10-01 to 2026-10-19, excluding Equifax,
TransUnion and Experian (filtered by the API); no product or state filter. By year: 2023
(Oct–Dec) 81,855; 2024 436,025; 2025 771,736; 2026 (to 19 Oct) 680,332. FDIC: 106 quarters,
2000 Q1 – 2026 Q2.

## dbt (via Cosmos, one Airflow task per model)
- 12 models built; 89 data tests: **87 passed, 2 warned, 0 errors**; 5 of 5 source freshness checks passed.
- Warnings (real outliers, configured as warn): `cre_to_loans_pct` outside 0–100 (132 rows),
  `tier1_leverage_pct` outside −50–100 (672 rows).

## Entity resolution (CFPB company → FDIC bank)
- 591,795 of 1,969,948 complaints matched to an FDIC bank = **30.0%**; 142 of 5,387 companies.
- By method: holding company 88 companies / 364,502 complaints; bank name 50 / 189,430;
  manual override 4 / 37,863; unmatched 5,245 / 1,378,153 (mostly not FDIC banks).

## Cost
- Snowflake (estimated from the pipeline user's query history, XSMALL = 1 credit/hour):
  ~0.20 credits for this run; ~0.42 since the project started.
- Databricks: 2.32 DBU recorded for 2026-10-20 in `system.billing.usage` at report time.

## Spot checks against the live FDIC / CFPB APIs
| Check | Pipeline | Source | Result |
|---|---|---|---|
| JPMorgan Chase Bank (CERT 628) total assets, 2026-06-30 | $4,091,315,000k | $4,091,315,000k | match |
| Wells Fargo Bank (CERT 3511) total assets, 2008-12-31 | $538,958,000k | $538,958,000k | match |
| CFPB complaints, Jan 2025, bureaus excluded | 123,553 | 123,553 | match |
| CFPB complaints, Jul 2026, bureaus excluded | 70,900 | 70,900 | match |
| FDIC failed-bank list | 4,117 | 4,117 | match |

`tests/e2e`: 17 of 17 passed.

## Model (`bank_health.ml.distress_model`)
Gradient boosting, trained 2001–2019, model chosen on 2017–2019, tested once on 2020–2024.

| Test 2020–2024 | v3 (retired) | **v4 (@champion)** |
|---|---|---|
| ROC-AUC | 0.855 | **0.854** |
| PR-AUC | 0.360 | **0.357** |
| Distress cases captured in riskiest 10% | 53.8% | **53.5%** |
| Brier score | 0.0438 | **0.0438** |

Test-period distress base rate: **5.6%** (4,859 of 86,558 bank-quarters). A random ranking's
PR-AUC equals the base rate, so 0.357 is about 6.4× chance.
Logistic-regression baseline (v4 run): ROC-AUC 0.742, PR-AUC 0.222, top-10% capture 39.6%.

Scores for 2026-06-30 (4,257 banks): High 195, Elevated 388, Watch 971, Low 2,330,
Already in distress 373.
