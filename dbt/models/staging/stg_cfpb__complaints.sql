-- One row per CFPB complaint. cert is the FDIC bank matched in Databricks silver
-- (NULL when the company is not an FDIC-insured bank or could not be matched).
select
    cast(complaint_id as integer)         as complaint_id,
    date_received,
    -- quarter-end the complaint belongs to, to line up with FDIC report dates
    last_day(date_received, 'quarter')    as quarter_end_date,
    date_sent_to_company,
    company,
    cast(cert as integer)                 as cert,
    match_method,
    -- a product the marts don't know (or a missing one) becomes 'Unmapped', never dropped
    case
        when product in (
            {%- for p in var('cfpb_known_products') %}
            '{{ p | replace("'", "''") }}'{{ "," if not loop.last }}
            {%- endfor %}
        ) then product
        else 'Unmapped'
    end                                   as product,
    product                               as product_original,
    sub_product,
    issue,
    sub_issue,
    company_response,
    company_public_response,
    timely_response,
    submitted_via,
    state_code,
    zip_code,
    tags,
    _loaded_at
from {{ source('raw', 'cfpb_complaints') }}
