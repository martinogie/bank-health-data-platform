{#
  Use the folder's schema (STAGING / MARTS) exactly as written.
  dbt's default would prefix the target schema, giving e.g. STAGING_MARTS.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim | upper }}
    {%- endif -%}
{%- endmacro %}
