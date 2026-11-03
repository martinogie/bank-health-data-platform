# M-08: model monitoring in dev (for review before the alert goes on)

`07_monitor` run in dev on 3 Nov 2026, reading **prod gold read-only** (dev gold has only 2 quarters), model bank_health.ml.distress_model v5, latest quarter 2026-06-30. Raw output: [m08_monitoring_dev.json](m08_monitoring_dev.json).

History: `ml.risk_score_history` (dev) filled with 716,566 rescored bank-quarters plus 4,222 from the run (720,788 rows). Quarters up to 2019 are marked in-sample.

**Would alert: nothing** (the rules: score PSI above 0.25, or 3+ features above 0.25, vs the previous 4 quarters; a judged quarter's top-10% capture more than 10 points below 52.6%).

## PSI, latest quarter vs the previous 4 quarters (the alert comparison)

| | PSI | Level |
|---|---|---|
| **score** | **0.0079** | stable |
| net_interest_margin_pct | 0.0442 | stable |
| net_income_qtr_to_assets_pct | 0.0380 | stable |
| roa_pct | 0.0342 | stable |
| loan_growth_qoq_pct | 0.0251 | stable |
| efficiency_ratio_pct | 0.0166 | stable |
| roe_pct | 0.0133 | stable |
| equity_to_assets_pct | 0.0114 | stable |
| asset_growth_yoy_pct | 0.0078 | stable |
| noncurrent_change_4q_pp | 0.0073 | stable |
| noncurrent_change_1q_pp | 0.0071 | stable |

All 31 features: stable (below 0.10). The 10 largest are listed.

## PSI, latest quarter vs 2001–2019 training (reported, never alerts)

| | PSI | Level |
|---|---|---|
| **score** | **0.3229** | large |
| roa_change_4q_pp | 0.7551 | large |
| log_total_assets | 0.6727 | large |
| noncurrent_change_4q_pp | 0.5693 | large |
| asset_growth_yoy_pct | 0.5180 | large |
| tier1_change_4q_pp | 0.4972 | large |
| tier1_leverage_pct | 0.4781 | large |
| past_due_30_89_to_loans_pct | 0.4326 | large |
| consumer_to_loans_pct | 0.4252 | large |
| ore_to_assets_pct | 0.3900 | large |
| deposit_growth_yoy_pct | 0.3568 | large |
| net_chargeoff_rate_pct | 0.2530 | large |
| net_income_qtr_to_assets_pct | 0.2185 | moderate |

Features: 11 large, 10 moderate, 10 stable. Expected after 20 years (bank sizes, rates, growth); this is why it is reported, not alerted on.

## Backtest: realised results per quarter (from 2020; outcome known)

2020–2024 are v5's out-of-time **test** quarters (used to choose the model); 2025 is new. All quarters have at least 30 positives, so all are judged.

| Quarter | Bank-quarters | Positives | PR-AUC | Top-10% capture |
|---|---|---|---|---|
| 2020-03-31 | 4,507 | 268 | 0.359 | 50.7% |
| 2020-06-30 | 4,504 | 237 | 0.347 | 57.4% |
| 2020-09-30 | 4,505 | 211 | 0.338 | 57.8% |
| 2020-12-31 | 4,447 | 187 | 0.287 | 59.9% |
| 2021-03-31 | 4,435 | 193 | 0.326 | 57.0% |
| 2021-06-30 | 4,426 | 202 | 0.331 | 55.9% |
| 2021-09-30 | 4,415 | 194 | 0.266 | 54.1% |
| 2021-12-31 | 4,391 | 191 | 0.319 | 59.2% |
| 2022-03-31 | 4,363 | 190 | 0.267 | 52.1% |
| 2022-06-30 | 4,350 | 174 | 0.203 | 47.7% |
| 2022-09-30 | 4,359 | 204 | 0.252 | 45.1% |
| 2022-12-31 | 4,362 | 258 | 0.267 | 46.5% |
| 2023-03-31 | 4,318 | 283 | 0.385 | 50.5% |
| 2023-06-30 | 4,284 | 308 | 0.398 | 51.6% |
| 2023-09-30 | 4,240 | 316 | 0.420 | 53.8% |
| 2023-12-31 | 4,168 | 283 | 0.420 | 54.4% |
| 2024-03-31 | 4,115 | 275 | 0.374 | 50.5% |
| 2024-06-30 | 4,082 | 281 | 0.405 | 49.5% |
| 2024-09-30 | 4,028 | 253 | 0.355 | 51.8% |
| 2024-12-31 | 3,997 | 253 | 0.368 | 55.7% |
| 2025-03-31 | 3,908 | 226 | 0.346 | 49.1% |
| 2025-06-30 | 3,894 | 236 | 0.367 | 55.9% |

Capture ranges from 45.1% to 59.9% (test result 52.6%; alert line 42.6%).
