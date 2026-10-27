{#
  Rows whose value is outside [min_value, max_value] (NULLs ignored), returned WITH their key
  columns. With store_failures, the stored table then says which rows failed (e.g. which bank in
  which quarter), not only the values; the run log samples those keys into
  OPS.DBT_WARNINGS_AUDIT (macros/record_dbt_results.sql). Used for real-world outliers that are
  kept and only warned about (finding D-05; why they're kept: docs/model_card.md).
#}
{% test value_in_range(model, column_name, min_value=none, max_value=none, key_columns=[]) %}
select
    {%- for k in key_columns %}
    {{ k }},
    {%- endfor %}
    {{ column_name }} as value
from {{ model }}
where {{ column_name }} is not null
  and (
        false
        {%- if min_value is not none %} or {{ column_name }} < {{ min_value }}{% endif %}
        {%- if max_value is not none %} or {{ column_name }} > {{ max_value }}{% endif %}
  )
{% endtest %}
