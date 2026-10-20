-- One row per bank: the model's distress probability for its latest call report.
select
    cast(cert as integer)                 as cert,
    report_date,
    distress_probability,
    risk_percentile,
    cast(risk_rank as integer)            as risk_rank,
    risk_band,
    is_distressed                         as already_in_distress,
    reason_1,
    reason_2,
    reason_3,
    model_name,
    cast(model_version as integer)        as model_version,
    scored_at,
    _loaded_at
from {{ source('raw', 'ml_risk_scores') }}
