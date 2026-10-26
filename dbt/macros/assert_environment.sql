{#
  Stops dbt unless the target and BANK_HEALTH_ENV belong together:
    - BANK_HEALTH_ENV must be dev, ci or prod (no default)
    - the target must have the same name (no `--target prod` from a dev environment)
    - only prod may point at BANK_HEALTH; prod must point at BANK_HEALTH
    - ci builds only into a CI_ schema
  Called from generate_schema_name (so it runs while dbt parses, before anything is built) and
  from on-run-start (so it also runs when a cached parse skips the schema macro).
#}
{% macro assert_environment() -%}
    {%- set env = env_var('BANK_HEALTH_ENV', '') -%}
    {%- set database = target.database | upper -%}
    {%- if env not in ['dev', 'ci', 'prod'] -%}
        {{ exceptions.raise_compiler_error("BANK_HEALTH_ENV must be dev, ci or prod, got '" ~ env ~ "'. There is no default.") }}
    {%- endif -%}
    {%- if target.name != env -%}
        {{ exceptions.raise_compiler_error("dbt target '" ~ target.name ~ "' does not match BANK_HEALTH_ENV=" ~ env ~ ". Only the target for the current environment may be used.") }}
    {%- endif -%}
    {%- if env != 'prod' and database == 'BANK_HEALTH' -%}
        {{ exceptions.raise_compiler_error("BANK_HEALTH_ENV=" ~ env ~ " may not build into BANK_HEALTH: only prod writes prod objects.") }}
    {%- endif -%}
    {%- if env == 'prod' and database != 'BANK_HEALTH' -%}
        {{ exceptions.raise_compiler_error("the prod target must use database BANK_HEALTH, not " ~ database) }}
    {%- endif -%}
    {%- if env == 'ci' and not (target.schema | upper).startswith('CI_') -%}
        {{ exceptions.raise_compiler_error("the ci target must build into a CI_ schema, not " ~ target.schema) }}
    {%- endif -%}
{%- endmacro %}

{% macro environment_check_sql() -%}
    {{- assert_environment() -}}
    select 'BANK_HEALTH_ENV={{ target.name }} database={{ target.database }}' as environment_check
{%- endmacro %}
