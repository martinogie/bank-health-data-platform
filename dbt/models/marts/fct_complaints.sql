-- One row per CFPB complaint, with the matched FDIC bank (cert NULL when not a matched bank).
--
-- Incremental with change detection (finding D-02): only complaints that are new or whose values
-- changed (_row_hash: e.g. a new company response, or a new bank match) are merged, whatever their
-- age. Complaints that left the source are deleted afterwards, so the table always equals a full
-- rebuild. A full rebuild: the DAG's full_refresh setting (or dbt --full-refresh).
{{ config(
    materialized='incremental',
    unique_key='complaint_id',
    incremental_strategy='merge',
    on_schema_change='fail',
    post_hook="delete from {{ this }} t where not exists (
        select 1 from {{ ref('stg_cfpb__complaints') }} s where s.complaint_id = t.complaint_id)"
) }}

with source as (
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
        product_original,
        sub_product,
        issue,
        sub_issue,
        company_response,
        company_response = 'Closed with monetary relief'            as is_monetary_relief,
        timely_response,
        submitted_via,
        state_code
    from {{ ref('stg_cfpb__complaints') }}
),

hashed as (
    select *, hash(*) as _row_hash from source
)

select h.* from hashed h
{% if is_incremental() %}
where not exists (
    select 1 from {{ this }} t where t.complaint_id = h.complaint_id and t._row_hash = h._row_hash
)
{% endif %}
