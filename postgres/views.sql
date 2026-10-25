-- Browsing views for the PostgreSQL mirror. Recreated by scripts/mirror_to_postgres.py after
-- every load (loading drops the tables, which drops dependent views).
-- Definitions follow docs/investigations/model_credibility.md:
--   CBLR bank             CBLRIND = 1 (Community Bank Leverage Ratio, from 2020)
--   foreign-bank office   IBA = 1, or both capital ratios 0 with CBLRIND blank
--   new bank              under 3 years old at the data quarter

-- ------------------------------------------------------------------ lineage_counts
create or replace view explore.lineage_counts as
select dataset, bronze_rows, silver_rows, gold_rows, snowflake_raw_rows, snowflake_marts_rows, explanation
from (values
  (1, 'FDIC financials (bank-quarters)',
   (select count(*) from bronze.fdic_financials), (select count(*) from silver.fdic_financials),
   (select count(*) from gold.bank_quarter), (select count(*) from snowflake_raw.fdic_financials),
   (select count(*) from snowflake_marts.fct_bank_quarter),
   'bronze keeps every load, so quarters uploaded more than once (the test slice and the 2 quarters re-pulled '
   'for amendments) appear more than once; silver keeps the newest load per bank-quarter. Gold keeps only '
   'banks in the FDIC institutions list (drops call-report filers such as uninsured trust companies). '
   'RAW = silver export; MARTS fct_bank_quarter has the same scope as gold.'),
  (2, 'FDIC institutions (banks)',
   (select count(*) from bronze.fdic_institutions), (select count(*) from silver.fdic_institutions),
   null::bigint, (select count(*) from snowflake_raw.fdic_institutions),
   (select count(*) from snowflake_marts.dim_bank),
   'Each run downloads a full snapshot, and bronze keeps every snapshot loaded; silver keeps the newest row '
   'per bank. No gold table (attributes are joined into gold.bank_quarter). MARTS dim_bank = one row per bank.'),
  (3, 'FDIC failures',
   (select count(*) from bronze.fdic_failures), (select count(*) from silver.fdic_failures),
   null::bigint, (select count(*) from snowflake_raw.fdic_failures), null::bigint,
   'Full list each run; bronze keeps every load, silver the newest copy of each failure. No mart table: '
   'failure dates are columns in MARTS dim_bank.'),
  (4, 'CFPB complaints',
   (select count(*) from bronze.cfpb_complaints), (select count(*) from silver.cfpb_complaints),
   null::bigint, (select count(*) from snowflake_raw.cfpb_complaints),
   (select count(*) from snowflake_marts.fct_complaints),
   'Equifax, TransUnion and Experian are excluded by the CFPB API at download, so they are never in bronze. '
   'Months uploaded more than once (e.g. the September 2026 test month) are in bronze twice; silver keeps '
   'one row per complaint_id. Gold holds counts per bank-quarter, not complaints. RAW and MARTS = silver.'),
  (5, 'Risk scores (latest quarter)',
   null::bigint, null::bigint, (select count(*) from ml.risk_scores),
   (select count(*) from snowflake_raw.ml_risk_scores),
   (select count(*) from snowflake_marts.mart_early_warning),
   'Scored by 05_score into ml.risk_scores (shown in the gold column), one row per bank; exported to RAW '
   'and joined with scorecard figures in MARTS mart_early_warning.')
) v(ord, dataset, bronze_rows, silver_rows, gold_rows, snowflake_raw_rows, snowflake_marts_rows, explanation)
order by ord;

-- ------------------------------------------------------------------ top_risk_50
create or replace view explore.top_risk_50 as
select r.cert, r.bank_name, r.state_code,
       round(((r.report_date - i.established_date) / 365.25)::numeric, 1)            as age_years,
       round((g.total_assets_k / 1000)::numeric, 1)                                   as assets_musd,
       r.risk_band, round(r.distress_probability::numeric, 3)                          as probability,
       r.reason_1, r.reason_2, r.reason_3,
       f.cblrind                                                                       as cblr_flag,
       f.iba                                                                           as iba_flag,
       g.total_risk_based_capital_pct, g.tier1_leverage_pct,
       round(g.efficiency_ratio_pct::numeric, 1)                                       as efficiency_ratio_pct,
       r.report_date
from ml.risk_scores r
left join silver.fdic_institutions i on i.cert = r.cert
left join gold.bank_quarter g on g.cert = r.cert and g.report_date = r.report_date
left join explore.fdic_capital_flags f on f.cert = r.cert and f.report_date = r.report_date
where r.report_date = (select max(report_date) from ml.risk_scores)
order by r.distress_probability desc
limit 50;

-- ------------------------------------------------------------------ cblr_zero_capital
create or replace view explore.cblr_zero_capital as
select g.cert, g.bank_name, g.state_code, g.report_date,
       g.total_risk_based_capital_pct, g.tier1_leverage_pct, f.cblrind, f.iba,
       case when f.cblrind = 1 then 'CBLR bank: risk-weighted capital not reported'
            when f.iba = 1 or (g.tier1_leverage_pct = 0 and f.cblrind is null) then 'Foreign-bank office: no capital of its own'
            else 'other' end                                                           as why_zero,
       r.risk_band, round(r.distress_probability::numeric, 3)                          as probability
from gold.bank_quarter g
left join explore.fdic_capital_flags f on f.cert = g.cert and f.report_date = g.report_date
left join ml.risk_scores r on r.cert = g.cert and r.report_date = g.report_date
where g.report_date = (select max(report_date) from gold.bank_quarter)
  and g.total_risk_based_capital_pct = 0;

-- ------------------------------------------------------------------ foreign_offices
create or replace view explore.foreign_offices as
select g.cert, g.bank_name, g.city, g.state_code, g.report_date,
       round((g.total_assets_k / 1000)::numeric, 1)                                   as assets_musd,
       round((g.total_deposits_k / 1000)::numeric, 1)                                 as deposits_musd,
       g.tier1_leverage_pct, g.total_risk_based_capital_pct, f.iba, f.cblrind,
       r.risk_band, round(r.distress_probability::numeric, 3)                          as probability
from gold.bank_quarter g
left join explore.fdic_capital_flags f on f.cert = g.cert and f.report_date = g.report_date
left join ml.risk_scores r on r.cert = g.cert and r.report_date = g.report_date
where g.report_date = (select max(report_date) from gold.bank_quarter)
  and (f.iba = 1 or (g.tier1_leverage_pct = 0 and g.total_risk_based_capital_pct = 0 and f.cblrind is null));

-- ------------------------------------------------------------------ new_banks
create or replace view explore.new_banks as
select r.cert, r.bank_name, r.state_code, i.established_date,
       round(((r.report_date - i.established_date) / 365.25)::numeric, 1)            as age_years,
       round((g.total_assets_k / 1000)::numeric, 1)                                   as assets_musd,
       r.risk_band, round(r.distress_probability::numeric, 3)                          as probability,
       round(g.efficiency_ratio_pct::numeric, 1)                                       as efficiency_ratio_pct,
       g.roa_pct, g.net_income_qtr_k, r.reason_1, r.reason_2, r.reason_3, r.report_date
from ml.risk_scores r
join silver.fdic_institutions i on i.cert = r.cert
left join gold.bank_quarter g on g.cert = r.cert and g.report_date = r.report_date
where r.report_date = (select max(report_date) from ml.risk_scores)
  and r.report_date - i.established_date < 3 * 365.25
order by r.distress_probability desc;

-- ------------------------------------------------------------------ bank_history
-- One bank across the layers: select * from explore.bank_history where cert = 628 order by report_date;
create or replace view explore.bank_history as
select s.cert, s.report_date,
       (select count(*) from bronze.fdic_financials b
         where b.cert = s.cert::text and b.repdte = to_char(s.report_date, 'YYYYMMDD'))  as bronze_rows,
       s.total_assets_k            as silver_total_assets_k,
       s.net_income_ytd_k          as silver_net_income_ytd_k,
       s.noncurrent_loans_pct      as silver_noncurrent_loans_pct,
       s.tier1_leverage_pct        as silver_tier1_leverage_pct,
       s.total_risk_based_capital_pct as silver_total_rbc_pct,
       g.net_income_qtr_k          as gold_net_income_qtr_k,
       g.asset_size_tier           as gold_asset_size_tier,
       g.loan_growth_yoy_pct       as gold_loan_growth_yoy_pct,
       g.complaints                as gold_complaints,
       raw.total_assets_k          as snowflake_raw_total_assets_k,
       m.roa_pct                   as marts_roa_pct,
       m.complaints_per_1b_deposits as marts_complaints_per_1b_deposits,
       m.noncurrent_loans_peer_pctile as marts_ncl_peer_pctile,
       r.risk_band                 as score_risk_band,
       r.distress_probability      as score_probability
from silver.fdic_financials s
left join gold.bank_quarter g on g.cert = s.cert and g.report_date = s.report_date
left join snowflake_raw.fdic_financials raw on raw.cert = s.cert and raw.report_date = s.report_date
left join snowflake_marts.mart_bank_scorecard m on m.cert = s.cert and m.report_date = s.report_date
left join ml.risk_scores r on r.cert = s.cert and r.report_date = s.report_date;

-- ------------------------------------------------------------------ bronze_vs_silver_duplicates
-- Rows present more than once in bronze (the same file loaded again) and which copy silver kept.
create or replace view explore.bronze_vs_silver_duplicates as
with fin_dupes as (
  select cert, repdte from bronze.fdic_financials group by cert, repdte having count(*) > 1
), cfpb_dupes as (
  select complaint_id from bronze.cfpb_complaints group by complaint_id having count(*) > 1
)
select 'fdic_financials' as dataset, b.cert || ' / ' || b.repdte as row_key,
       b._source_file, b._file_modified_at, b._ingested_at,
       (b._source_file = s._source_file and b._ingested_at = s._ingested_at) as kept_by_silver
from bronze.fdic_financials b
join fin_dupes d on d.cert = b.cert and d.repdte = b.repdte
left join silver.fdic_financials s on s.cert::text = b.cert and to_char(s.report_date, 'YYYYMMDD') = b.repdte
union all
select 'cfpb_complaints', b.complaint_id,
       b._source_file, b._file_modified_at, b._ingested_at,
       (b._source_file = s._source_file and b._ingested_at = s._ingested_at)
from bronze.cfpb_complaints b
join cfpb_dupes d on d.complaint_id = b.complaint_id
left join silver.cfpb_complaints s on s.complaint_id::text = b.complaint_id;
