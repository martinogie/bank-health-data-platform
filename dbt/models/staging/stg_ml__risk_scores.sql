-- One row per bank: the model's distress probability for its latest call report.
-- Banks under 3 years old and offices of foreign banks are not scored (probability NULL).
select
    cast(cert as integer)                 as cert,
    report_date,
    distress_probability,
    risk_percentile,
    cast(risk_rank as integer)            as risk_rank,
    risk_band,
    is_distressed                         as already_in_distress,
    is_new_bank,
    is_foreign_office,
    cblr_reporter,
    bank_age_years,
    reason_1,
    reason_2,
    reason_3,
    model_name,
    cast(model_version as integer)        as model_version,
    scored_at,
    _loaded_at
from {{ source('raw', 'ml_risk_scores') }}
