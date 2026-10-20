-- One row per bank failure. A cert can appear more than once (e.g. assistance, then failure).
select
    cast(failure_id as integer)           as failure_id,
    cast(cert as integer)                 as cert,
    bank_name,
    city,
    state_code,
    fail_date,
    resolution_type,
    charter_class,
    total_assets_k,
    total_deposits_k,
    estimated_loss_k,
    acquirer,
    _loaded_at
from {{ source('raw', 'fdic_failures') }}
