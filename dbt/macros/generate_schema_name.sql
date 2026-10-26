{#
  prod and dev: use the folder's schema (STAGING / MARTS) exactly as written. dbt's default would
  prefix the target schema, giving e.g. STAGING_MARTS. dev is kept apart by its database
  (BANK_HEALTH_DEV), so its schema names are the same as prod's.
  ci: everything goes into the one temporary CI_<run_id> schema, which CI drops afterwards.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{- assert_environment() -}}
    {%- if target.name == 'ci' or custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim | upper }}
    {%- endif -%}
{%- endmacro %}
