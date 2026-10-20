-- One row per FDIC institution: descriptive attributes, failure history,
-- whether any CFPB complaints were matched to it, and its latest size tier.
with institutions as (
    select * from {{ ref('stg_fdic__institutions') }}
),

failures as (
    select
        cert,
        min(fail_date) as first_fail_date,
        max(fail_date) as last_fail_date,
        count(*)       as failure_records
    from {{ ref('stg_fdic__failures') }}
    where cert is not null
    group by cert
),

latest_financials as (
    select cert, report_date, total_assets_k
    from {{ ref('stg_fdic__financials') }}
    qualify row_number() over (partition by cert order by report_date desc) = 1
),

complaint_banks as (
    select distinct cert from {{ ref('stg_cfpb__complaints') }} where cert is not null
)

select
    i.cert,
    i.bank_name,
    i.city,
    i.state_code,
    i.state_name,
    i.bank_class,
    i.primary_regulator,
    i.specialization,
    i.holding_company,
    i.is_active,
    i.established_date,
    i.end_date,
    i.offices,
    i.website,
    f.cert is not null                                as has_failed,
    f.first_fail_date,
    f.last_fail_date,
    coalesce(f.failure_records, 0)                    as failure_records,
    cb.cert is not null                               as has_cfpb_complaints,
    lf.report_date                                    as latest_report_date,
    lf.total_assets_k                                 as latest_total_assets_k,
    {{ asset_size_tier('lf.total_assets_k') }}        as latest_asset_size_tier
from institutions i
left join failures f           on f.cert = i.cert
left join latest_financials lf on lf.cert = i.cert
left join complaint_banks cb   on cb.cert = i.cert
