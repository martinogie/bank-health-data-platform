-- One row per CFPB complaint, with the matched FDIC bank (cert NULL when not a matched bank).
select
    complaint_id,
    date_received,
    quarter_end_date,
    date_sent_to_company,
    datediff(day, date_received, date_sent_to_company)          as days_to_send_to_company,
    company,
    cert,
    match_method,
    cert is not null                                            as is_matched_bank,
    product,
    sub_product,
    issue,
    sub_issue,
    company_response,
    company_response = 'Closed with monetary relief'            as is_monetary_relief,
    timely_response,
    submitted_via,
    state_code
from {{ ref('stg_cfpb__complaints') }}
