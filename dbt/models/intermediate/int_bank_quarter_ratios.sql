-- Derived ratios per bank per quarter, plus prior-quarter values for QoQ changes.
-- Prior periods are joined on the exact quarter-end, so a missing quarter gives NULL
-- rather than comparing against the wrong period.
--
-- FDIC files a capital ratio as 0 when it is not reported. Here those become NULL:
--   CBLR banks (from 2020) report no risk-based capital ratio;
--   US offices of foreign banks (IBA flag, FDIC class OI, or the reviewed override list in
--   dbt_project.yml) report no capital ratios at all.
-- Everyone else keeps the reported values, including real zero or negative capital.
with reported as (
    select * from {{ ref('stg_fdic__financials') }}
),

fin as (
    select
        r.* exclude (tier1_leverage_pct, total_risk_based_capital_pct),
        coalesce(r.cblr_indicator = 1, false)                              as cblr_reporter,
        coalesce(r.iba_flag = 1 or i.bank_class = 'OI'
                 or r.cert in ({{ var('foreign_office_overrides') | join(', ') }}), false) as is_foreign_office,
        iff(is_foreign_office, null, r.tier1_leverage_pct)                 as tier1_leverage_pct,
        iff(cblr_reporter or is_foreign_office, null, r.total_risk_based_capital_pct) as total_risk_based_capital_pct
    from reported r
    left join {{ ref('stg_fdic__institutions') }} i on i.cert = r.cert
),

prev_q as (
    -- each row moved forward one quarter = "previous quarter" for the later row
    select
        cert,
        last_day(dateadd(month, 3, report_date)) as report_date,
        net_income_ytd_k         as net_income_ytd_k_prev_q,
        gross_loans_k            as gross_loans_k_prev_q,
        total_deposits_k         as total_deposits_k_prev_q,
        roa_pct                  as roa_pct_prev_q,
        noncurrent_loans_pct     as noncurrent_loans_pct_prev_q,
        tier1_leverage_pct       as tier1_leverage_pct_prev_q,
        efficiency_ratio_pct     as efficiency_ratio_pct_prev_q
    from fin
),

prev_y as (
    select
        cert,
        last_day(dateadd(month, 12, report_date)) as report_date,
        gross_loans_k            as gross_loans_k_prev_y,
        total_deposits_k         as total_deposits_k_prev_y,
        total_assets_k           as total_assets_k_prev_y
    from fin
),

joined as (
    select fin.*, prev_q.* exclude (cert, report_date), prev_y.* exclude (cert, report_date)
    from fin
    left join prev_q on prev_q.cert = fin.cert and prev_q.report_date = fin.report_date
    left join prev_y on prev_y.cert = fin.cert and prev_y.report_date = fin.report_date
)

select
    cert,
    report_date,
    year(report_date)                                     as report_year,
    quarter(report_date)                                  as report_quarter,

    total_assets_k,
    total_deposits_k,
    gross_loans_k,
    total_equity_k,
    roa_pct,
    roe_pct,
    net_interest_margin_pct,
    efficiency_ratio_pct,
    noncurrent_loans_pct,
    net_chargeoff_rate_pct,
    tier1_leverage_pct,
    total_risk_based_capital_pct,
    cblr_reporter,
    is_foreign_office,
    brokered_deposits_k,

    -- FDIC income is year-to-date: Q1 as reported, later quarters minus the prior quarter
    case when quarter(report_date) = 1 then net_income_ytd_k
         else net_income_ytd_k - net_income_ytd_k_prev_q end              as net_income_qtr_k,

    round((gross_loans_k / nullif(gross_loans_k_prev_q, 0) - 1) * 100, 4)       as loan_growth_qoq_pct,
    round((gross_loans_k / nullif(gross_loans_k_prev_y, 0) - 1) * 100, 4)       as loan_growth_yoy_pct,
    round((total_deposits_k / nullif(total_deposits_k_prev_q, 0) - 1) * 100, 4) as deposit_growth_qoq_pct,
    round((total_deposits_k / nullif(total_deposits_k_prev_y, 0) - 1) * 100, 4) as deposit_growth_yoy_pct,
    round((total_assets_k / nullif(total_assets_k_prev_y, 0) - 1) * 100, 4)     as asset_growth_yoy_pct,

    -- CRE concentration: construction + multifamily + non-farm non-residential.
    -- Missing only if all three parts are missing (then NULL, never 0%).
    case when coalesce(construction_loans_k, multifamily_loans_k, nonfarm_nonres_loans_k) is not null
         then coalesce(construction_loans_k, 0) + coalesce(multifamily_loans_k, 0)
              + coalesce(nonfarm_nonres_loans_k, 0) end                  as cre_loans_k,
    round(cre_loans_k / nullif(gross_loans_k, 0) * 100, 4)               as cre_to_loans_pct,
    round(cre_loans_k / nullif(total_equity_k, 0) * 100, 4)              as cre_to_equity_pct,
    round(total_equity_k / nullif(total_assets_k, 0) * 100, 4)           as equity_to_assets_pct,
    round(gross_loans_k / nullif(total_deposits_k, 0) * 100, 4)          as loans_to_deposits_pct,
    {{ asset_size_tier('total_assets_k') }}                              as asset_size_tier,

    -- quarter-over-quarter changes in percentage points
    roa_pct - roa_pct_prev_q                                             as roa_change_qoq_pp,
    noncurrent_loans_pct - noncurrent_loans_pct_prev_q                   as noncurrent_loans_change_qoq_pp,
    tier1_leverage_pct - tier1_leverage_pct_prev_q                       as tier1_leverage_change_qoq_pp,
    efficiency_ratio_pct - efficiency_ratio_pct_prev_q                   as efficiency_ratio_change_qoq_pp
from joined
