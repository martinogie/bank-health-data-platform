-- Browsing views for the PostgreSQL mirror. Recreated by scripts/mirror_to_postgres.py after
-- every load (loading drops the tables, which drops dependent views).
-- Definitions (model v5, see docs/model_card.md), carried in gold.bank_quarter:
--   CBLR bank             cblr_reporter (FDIC CBLRIND = 1, from 2020)
--   foreign-bank office   is_foreign_office (IBA flag, FDIC class OI, or the reviewed override list)
--   new bank              under 3 years old at the data quarter (is_new_bank in ml.risk_scores)
-- Gold leaves capital ratios a bank doesn't report empty; silver keeps FDIC's 0.

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
       g.cblr_reporter, g.is_foreign_office,
       g.total_risk_based_capital_pct, g.tier1_leverage_pct,
       round(g.efficiency_ratio_pct::numeric, 1)                                       as efficiency_ratio_pct,
       r.report_date
from ml.risk_scores r
left join silver.fdic_institutions i on i.cert = r.cert
left join gold.bank_quarter g on g.cert = r.cert and g.report_date = r.report_date
where r.report_date = (select max(report_date) from ml.risk_scores)
  and r.risk_band in ('High', 'Elevated', 'Watch', 'Low')
order by r.distress_probability desc
limit 50;

-- ------------------------------------------------------------------ cblr_zero_capital
-- Banks whose FDIC total risk-based capital is 0 this quarter (silver), and what gold does with it
create or replace view explore.cblr_zero_capital as
select g.cert, g.bank_name, g.state_code, g.report_date,
       s.total_risk_based_capital_pct                                                  as fdic_total_rbc_pct,
       g.total_risk_based_capital_pct                                                  as gold_total_rbc_pct,
       s.tier1_leverage_pct                                                            as fdic_tier1_pct,
       g.tier1_leverage_pct                                                            as gold_tier1_pct,
       g.cblr_indicator, g.iba_flag, g.bank_class,
       case when g.is_foreign_office then 'Foreign-bank office (' || g.foreign_office_rule || '): no capital ratios of its own'
            when g.cblr_reporter then 'CBLR bank: risk-weighted capital not reported'
            else 'reported as 0 (kept: real zero capital)' end                         as why_zero,
       r.risk_band, round(r.distress_probability::numeric, 3)                          as probability
from gold.bank_quarter g
join silver.fdic_financials s on s.cert = g.cert and s.report_date = g.report_date
left join ml.risk_scores r on r.cert = g.cert and r.report_date = g.report_date
where g.report_date = (select max(report_date) from gold.bank_quarter)
  and s.total_risk_based_capital_pct = 0;

-- ------------------------------------------------------------------ foreign_offices
create or replace view explore.foreign_offices as
select g.cert, g.bank_name, g.city, g.state_code, g.report_date,
       round((g.total_assets_k / 1000)::numeric, 1)                                   as assets_musd,
       round((g.total_deposits_k / 1000)::numeric, 1)                                 as deposits_musd,
       g.foreign_office_rule, g.iba_flag, g.bank_class,
       r.risk_band, round(r.distress_probability::numeric, 3)                          as probability
from gold.bank_quarter g
left join ml.risk_scores r on r.cert = g.cert and r.report_date = g.report_date
where g.report_date = (select max(report_date) from gold.bank_quarter)
  and g.is_foreign_office;

-- ------------------------------------------------------------------ new_banks
create or replace view explore.new_banks as
select r.cert, r.bank_name, r.state_code, i.established_date,
       round(r.bank_age_years::numeric, 1)                                             as age_years,
       round((g.total_assets_k / 1000)::numeric, 1)                                   as assets_musd,
       r.risk_band, round(r.distress_probability::numeric, 3)                          as probability,
       round(g.efficiency_ratio_pct::numeric, 1)                                       as efficiency_ratio_pct,
       g.roa_pct, g.net_income_qtr_k, r.reason_1, r.reason_2, r.reason_3, r.report_date
from ml.risk_scores r
join silver.fdic_institutions i on i.cert = r.cert
left join gold.bank_quarter g on g.cert = r.cert and g.report_date = r.report_date
where r.report_date = (select max(report_date) from ml.risk_scores)
  and r.is_new_bank
order by r.bank_age_years;

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

-- ------------------------------------------------------------------ v4_vs_v5
-- Per bank, 2026 Q2: model v4 (snapshot ml.risk_scores_v4, taken before promotion) next to v5.
create or replace view explore.v4_vs_v5 as
with bands(band, ord) as (values ('High', 1), ('Elevated', 2), ('Watch', 3), ('Low', 4),
                                 ('Already in distress', 0))
select coalesce(n.cert, o.cert)                                                        as cert,
       coalesce(n.bank_name, o.bank_name)                                              as bank_name,
       coalesce(n.state_code, o.state_code)                                            as state_code,
       o.risk_band                                                                     as v4_band,
       n.risk_band                                                                     as v5_band,
       case when o.risk_band = n.risk_band then 'same band'
            when n.risk_band like '%not scored' then 'now not scored'
            when bo.ord is null or bn.ord is null then 'changed'
            when bn.ord < bo.ord then 'riskier band'
            else 'safer band' end                                                      as band_change,
       round(o.distress_probability::numeric, 3)                                       as v4_probability,
       round(n.distress_probability::numeric, 3)                                       as v5_probability,
       round((n.distress_probability - o.distress_probability)::numeric, 3)            as probability_change,
       o.risk_rank                                                                     as v4_rank,
       n.risk_rank                                                                     as v5_rank,
       o.reason_1 as v4_reason_1, o.reason_2 as v4_reason_2, o.reason_3 as v4_reason_3,
       n.reason_1 as v5_reason_1, n.reason_2 as v5_reason_2, n.reason_3 as v5_reason_3,
       n.is_new_bank, n.is_foreign_office, n.cblr_reporter, round(n.bank_age_years::numeric, 1) as age_years
from ml.risk_scores n
full join ml.risk_scores_v4 o on o.cert = n.cert
left join bands bo on bo.band = o.risk_band
left join bands bn on bn.band = n.risk_band;
