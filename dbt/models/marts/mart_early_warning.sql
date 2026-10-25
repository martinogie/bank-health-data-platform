-- Early-warning list: each bank's latest distress probability, band and reasons, next to its
-- scorecard figures and complaints for the same quarter. One row per bank.
-- Portfolio model on public data, not a supervisory rating.
with scores as (
    select * from {{ ref('stg_ml__risk_scores') }}
),

scorecard as (
    select * from {{ ref('mart_bank_scorecard') }}
)

select
    s.cert,
    d.bank_name,
    d.city,
    d.state_code,
    d.holding_company,
    d.primary_regulator,
    s.report_date,
    sc.asset_size_tier,
    sc.total_assets_k,

    s.distress_probability,
    s.risk_percentile,
    s.risk_rank,
    s.risk_band,
    s.already_in_distress,
    s.is_new_bank,
    s.is_foreign_office,
    s.cblr_reporter,
    s.bank_age_years,
    s.reason_1,
    s.reason_2,
    s.reason_3,

    sc.tier1_leverage_pct,
    sc.tier1_leverage_change_qoq_pp,
    sc.noncurrent_loans_pct,
    sc.noncurrent_loans_change_qoq_pp,
    sc.roa_pct,
    sc.roa_change_qoq_pp,
    sc.efficiency_ratio_pct,
    sc.cre_to_equity_pct,
    sc.loan_growth_yoy_pct,

    sc.complaint_data_covered,
    sc.complaints,
    sc.complaints_change_qoq,
    sc.complaints_per_1b_deposits,
    sc.complaints_per_1b_deposits_peer_pctile,

    s.model_name,
    s.model_version,
    s.scored_at
from scores s
inner join {{ ref('dim_bank') }} d on d.cert = s.cert
left join scorecard sc on sc.cert = s.cert and sc.report_date = s.report_date
