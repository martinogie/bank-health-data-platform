-- Complaint counts per matched bank per quarter.
-- Only complaints whose company was matched to an FDIC bank (cert not null).
select
    cert,
    quarter_end_date                                                as report_date,
    count(*)                                                        as complaints,
    count_if(timely_response = false)                               as complaints_untimely,
    count_if(company_response = 'Closed with monetary relief')     as complaints_monetary_relief,
    count(distinct product)                                         as complaint_products
from {{ ref('stg_cfpb__complaints') }}
where cert is not null
group by 1, 2
