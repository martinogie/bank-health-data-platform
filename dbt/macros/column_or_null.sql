{#
  A source column added by a newer pipeline version, or NULL while the source doesn't have it yet.
  Prod RAW gets new columns only when the new scoring code first runs, but dbt (and slim CI, which
  reads prod's sources) can see the new model before that. Usage, in a select list:
      {{ column_or_null(source('raw', 'ml_risk_scores'), 'reason_method') }},
  At execution time it checks the relation's columns; while parsing it just names the column.
#}
{% macro column_or_null(relation, column, data_type='varchar') %}
    {%- if execute -%}
        {%- set names = adapter.get_columns_in_relation(relation) | map(attribute='name') | map('upper') | list -%}
        {%- if column | upper in names -%}
            {{ column }}
        {%- else -%}
            cast(null as {{ data_type }}) as {{ column }}
        {%- endif -%}
    {%- else -%}
        {{ column }}
    {%- endif -%}
{% endmacro %}
