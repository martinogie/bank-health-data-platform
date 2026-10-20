-- One row per FDIC institution (active and inactive), latest snapshot.
select
    cast(cert as integer)                 as cert,
    bank_name,
    city,
    state_code,
    state_name,
    zip_code,
    is_active,
    bank_class,
    primary_regulator,
    specialization,
    holding_company,
    established_date,
    insured_date,
    end_date,
    total_assets_k                        as latest_total_assets_k,
    total_deposits_k                      as latest_total_deposits_k,
    cast(offices as integer)              as offices,
    website,
    as_of_date,
    _loaded_at
from {{ source('raw', 'fdic_institutions') }}
