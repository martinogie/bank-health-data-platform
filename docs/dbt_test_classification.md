# dbt tests: critical or warn (Step 6, for review)

**Status: proposal. Nothing here changes a test until you approve it.** All 96 tests are listed
below, taken from the parsed dbt project on 27 Oct 2026.

- **Critical** (`severity: error`): the data is wrong or incomplete, so it must not be published.
  The task fails, an alert goes out, and later steps stop.
- **Warn** (`severity: warn`): a real-world value that is unusual but true. The rows are kept,
  counted, and sampled in `OPS.DBT_WARNINGS_AUDIT` (finding D-05). The health page shows the count.

The rule I used: a test is **critical** if failing it means *our pipeline* made a mistake (a lost
key, a duplicate, a broken join, a stale load, a value that can't exist). It is **warn** if
failing it can mean *the bank reported something unusual*.

## What "stop before publishing" means today, and a gap

Scoring runs in Databricks before dbt. **Scoring** is protected by the Databricks notebooks' own
checks (`dq_finish()` stops the job on any error-level check) and by `sanity_check_top20`. **The
marts** are built by dbt after scoring. Cosmos runs each model's tests right after that model:

- A critical test failing on a **source, staging or intermediate** model stops every mart that
  depends on it. Those marts keep last week's version: **not published**.
- A critical test failing on a **mart itself** runs after that mart was rebuilt. The failure stops
  everything downstream (later marts, the watchlist export) and alerts, but **that one mart has
  already been replaced**.

To close the gap, the marts would be built into a side schema, tested, and then swapped in one
atomic step (`ALTER SCHEMA MARTS SWAP WITH MARTS_NEXT`), a "write, audit, publish" pattern. That
fits Phase 3 (finding D-02, idempotent loads). **Your decision:** close it in Phase 3, or now.

## Proposed: no change to the classes

All 92 current error tests are pipeline-correctness checks, and stay **critical**. The 4 current
warnings are real-world values, and stay **warn**. The one change in this phase is how the
warnings are surfaced: stored failures with row keys, the audit table, and the health page.

### Warn (4)

| Model | Column | Test | Why warn |
|---|---|---|---|
| fct_bank_quarter | tier1_leverage_pct | value_in_range −50 … 100 (stores failing rows) | tiny or brand-new banks report extreme ratios; 672 rows today; true as filed |
| fct_bank_quarter | cre_to_loans_pct | value_in_range 0 … 100 (stores failing rows) | CRE can exceed net loans in real call reports; 132 rows today |
| fct_complaints | days_to_send_to_company | ≥ 0 | the CFPB occasionally records the send date before receipt; a source quirk |
| mart_early_warning | asset_size_tier | not_null | NULL only if a bank's latest quarter is missing from the scorecard; the bank is still scored |

### Critical: sources (RAW, loaded from Databricks) (23)

| Source | Tests |
|---|---|
| fdic_financials | unique (cert, report_date); not_null cert, report_date; recency of `_loaded_at` ≤ 15 days; recency of `report_date` ≤ 200 days |
| fdic_institutions | unique + not_null cert; not_null bank_name; recency `_loaded_at` ≤ 15 days |
| fdic_failures | unique + not_null failure_id; not_null fail_date; recency `_loaded_at` ≤ 15 days |
| cfpb_complaints | unique + not_null complaint_id; not_null company, date_received; recency `_loaded_at` ≤ 15 days; recency `date_received` ≤ 45 days |
| ml_risk_scores | unique + not_null cert; not_null distress_probability; recency `_loaded_at` ≤ 15 days |

### Critical: staging and intermediate (28)

| Model | Tests |
|---|---|
| stg_fdic__financials | unique (cert, report_date); not_null cert, report_date, iba_flag; accepted_values cblr_indicator {0,1}, iba_flag {0,1} |
| stg_fdic__institutions | unique + not_null cert; not_null bank_name |
| stg_fdic__failures | unique + not_null failure_id; not_null fail_date |
| stg_cfpb__complaints | unique + not_null complaint_id; not_null date_received, match_method; accepted_values match_method; relationship cert → stg_fdic__institutions |
| stg_ml__risk_scores | unique + not_null cert; not_null distress_probability, risk_band; accepted_values risk_band; reason_1 never quotes total risk-based capital; relationship cert → stg_fdic__institutions |
| int_bank_quarter_ratios | unique (cert, report_date) |
| int_complaints_by_bank_quarter | unique (cert, report_date); complaints ≥ 1 |

### Critical: marts (41)

| Model | Tests |
|---|---|
| dim_bank | unique + not_null cert; not_null bank_name, has_failed; accepted_values latest_asset_size_tier |
| fct_bank_quarter | unique (cert, report_date); not_null cert, report_date, asset_size_tier, complaints; accepted_values asset_size_tier, report_quarter; total_assets_k ≥ 0; noncurrent_loans_pct 0 … 100; complaints ≥ 0; complaints_per_1b_deposits ≥ 0; relationship cert → dim_bank; `assert_unreported_capital_is_null`; `assert_complaint_counts_reconcile` |
| fct_complaints | unique + not_null complaint_id; not_null date_received; date_received between 2011-12-01 and today; accepted_values match_method; relationship cert → dim_bank |
| mart_bank_scorecard | unique (cert, report_date); five peer percentiles within 0 … 1; peers_in_tier ≥ 1 |
| mart_early_warning | unique + not_null cert; not_null bank_name, distress_probability, risk_band; distress_probability 0 … 1; risk_percentile 0 … 1; accepted_values risk_band; relationship cert → dim_bank |

## Questions for you

1. Approve the classes as above, or move any test (name it).
2. The publishing gap: close it in Phase 3 with write, audit, publish, or now?
