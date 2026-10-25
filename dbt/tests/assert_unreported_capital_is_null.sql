-- Capital ratios are never 0 when they are not reported.
-- 1. CBLR banks have no total risk-based capital ratio, and foreign-bank offices have neither
--    ratio, so those values must be NULL (FDIC files them as 0).
-- 2. A ratio that is missing at the source must never turn into 0 on the way to the marts.
-- Any row returned is a failure.
with fct as (
    select * from {{ ref('fct_bank_quarter') }}
),

src as (
    select cert, report_date, tier1_leverage_pct, total_risk_based_capital_pct
    from {{ ref('stg_fdic__financials') }}
)

select cert, report_date, 'unreported total risk-based capital is not NULL' as problem
from fct
where (cblr_reporter or is_foreign_office) and total_risk_based_capital_pct is not null

union all

select cert, report_date, 'foreign-bank office has a Tier 1 leverage value'
from fct
where is_foreign_office and tier1_leverage_pct is not null

union all

select f.cert, f.report_date, 'ratio missing at source became 0'
from fct f
inner join src s on s.cert = f.cert and s.report_date = f.report_date
where (s.tier1_leverage_pct is null and f.tier1_leverage_pct = 0)
   or (s.total_risk_based_capital_pct is null and f.total_risk_based_capital_pct = 0)
