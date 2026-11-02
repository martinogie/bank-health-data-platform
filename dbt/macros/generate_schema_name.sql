{#
  prod and dev: use the folder's schema (STAGING / MARTS) exactly as written. dbt's default would
  prefix the target schema, giving e.g. STAGING_MARTS. dev is kept apart by its database
  (BANK_HEALTH_DEV), so its schema names are the same as prod's.
  ci: everything goes into the one temporary CI_<run_id> schema, which CI drops afterwards.

  Write, audit, publish: the pipeline's dbt build sets var('marts_schema') = 'MARTS_NEXT', so the
  marts are built and tested in a clone; publish_marts then swaps it with MARTS. Every other dbt
  command (docs generate, which writes the manifest slim CI defers to; CI; local runs) leaves the
  var unset, so marts resolve to MARTS.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{- assert_environment() -}}
    {%- if target.name == 'ci' or custom_schema_name is none -%}
        {{ target.schema }}
    {%- elif (custom_schema_name | trim | upper) == 'MARTS' -%}
        {{ var('marts_schema', 'MARTS') | trim | upper }}
    {%- else -%}
        {{ custom_schema_name | trim | upper }}
    {%- endif -%}
{%- endmacro %}
