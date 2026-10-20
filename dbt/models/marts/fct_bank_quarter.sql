-- One row per bank per quarter: ratios and complaint counts.
-- Only institutions in FDIC's institutions dataset (same scope as Databricks gold).
-- Complaint columns are filled only for quarters fully covered by the complaint data;
-- otherwise NULL, so a partly-loaded quarter never looks artificially quiet.
with ratios as (
    select * from {{ ref('int_bank_quarter_ratios') }}
),

complaints as (
    select * from {{ ref('int_complaints_by_bank_quarter') }}
),

coverage as (
    select min(date_received) as first_complaint_date, max(date_received) as last_complaint_date
    from {{ ref('stg_cfpb__complaints') }}
),

banks as (
    select cert, has_cfpb_complaints from {{ ref('dim_bank') }}
)

select
    r.*,
    b.has_cfpb_complaints,
    coalesce(date_trunc('quarter', r.report_date) >= cov.first_complaint_date
             and r.report_date <= cov.last_complaint_date, false)        as complaint_data_covered,
    iff(complaint_data_covered, coalesce(c.complaints, 0), null)         as complaints,
    iff(complaint_data_covered, coalesce(c.complaints_untimely, 0), null) as complaints_untimely,
    iff(complaint_data_covered, coalesce(c.complaints_monetary_relief, 0), null) as complaints_monetary_relief,
    -- written out in full: a bare "complaints" here would mean c.complaints (not zero-filled)
    iff(complaint_data_covered and r.total_deposits_k > 0,
        round(coalesce(c.complaints, 0) / (r.total_deposits_k / 1e6), 4), null) as complaints_per_1b_deposits
from ratios r
inner join banks b on b.cert = r.cert
cross join coverage cov
left join complaints c on c.cert = r.cert and c.report_date = r.report_date
