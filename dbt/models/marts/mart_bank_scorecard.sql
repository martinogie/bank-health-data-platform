-- Bank scorecard per quarter: headline ratios, QoQ changes, complaints per $1B deposits,
-- and each bank's percentile within its asset-size peer group for that quarter
-- (0 = lowest value in the group, 1 = highest). Banks with a NULL metric get a NULL percentile.
{% set peer_metrics = [
    'roa_pct', 'noncurrent_loans_pct', 'tier1_leverage_pct',
    'efficiency_ratio_pct', 'cre_to_equity_pct', 'complaints_per_1b_deposits'
] %}

with fq as (
    select * from {{ ref('fct_bank_quarter') }}
),

prev as (
    select cert, last_day(dateadd(month, 3, report_date)) as report_date,
           complaints as complaints_prev_q
    from fq
)

select
    fq.cert,
    d.bank_name,
    d.state_code,
    d.holding_company,
    fq.report_date,
    fq.asset_size_tier,
    count(*) over (partition by fq.report_date, fq.asset_size_tier)  as peers_in_tier,

    fq.total_assets_k,
    fq.total_deposits_k,
    fq.net_income_qtr_k,
    fq.roa_pct,
    fq.roa_change_qoq_pp,
    fq.noncurrent_loans_pct,
    fq.noncurrent_loans_change_qoq_pp,
    fq.tier1_leverage_pct,
    fq.tier1_leverage_change_qoq_pp,
    fq.efficiency_ratio_pct,
    fq.efficiency_ratio_change_qoq_pp,
    fq.cre_to_equity_pct,
    fq.loan_growth_yoy_pct,
    fq.deposit_growth_yoy_pct,

    fq.complaint_data_covered,
    fq.has_cfpb_complaints,
    fq.complaints,
    p.complaints_prev_q,
    fq.complaints - p.complaints_prev_q                                as complaints_change_qoq,
    fq.complaints_per_1b_deposits,

    {% for m in peer_metrics %}
    iff(fq.{{ m }} is null, null,
        percent_rank() over (partition by fq.report_date, fq.asset_size_tier, fq.{{ m }} is null
                             order by fq.{{ m }}))                     as {{ m | replace('_pct', '') }}_peer_pctile
    {{- "," if not loop.last }}
    {% endfor %}
from fq
inner join {{ ref('dim_bank') }} d on d.cert = fq.cert
left join prev p on p.cert = fq.cert and p.report_date = fq.report_date
