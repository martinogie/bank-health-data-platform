-- Complaints counted in fct_bank_quarter must equal the individual matched complaints
-- in fct_complaints for the same bank-quarters. Returns rows only when they differ.
with counted as (
    select cert, report_date, complaints
    from {{ ref('fct_bank_quarter') }}
    where complaint_data_covered
),

individual as (
    select c.cert, c.quarter_end_date as report_date, count(*) as complaints
    from {{ ref('fct_complaints') }} c
    inner join counted k on k.cert = c.cert and k.report_date = c.quarter_end_date
    group by 1, 2
)

select k.cert, k.report_date, k.complaints as counted, coalesce(i.complaints, 0) as individual
from counted k
left join individual i on i.cert = k.cert and i.report_date = k.report_date
where k.complaints != coalesce(i.complaints, 0)
