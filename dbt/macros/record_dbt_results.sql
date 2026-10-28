{#
  on-run-end hook (dbt_project.yml): record what this dbt invocation did, for the run log and the
  health page (findings O-03, D-05).

  - <target database>.OPS.DBT_RESULTS: one row per model or test result, tagged with the Airflow
    run id (env var AIRFLOW_RUN_ID, set by the DAG; 'manual' otherwise). Cosmos runs dbt once per
    model, so one Airflow run writes many invocations; record_run adds them up.
  - <target database>.OPS.DBT_WARNINGS_AUDIT: for every test that WARNED, the failing row count
    and, when the test stores its failures with key columns (value_in_range), a sample of up to
    20 row keys. Warnings stay visible instead of scrolling past in a log.

  Skipped in CI (target ci) and while the OPS tables don't exist yet (snowflake/setup_ops.sql).
  Returns a harmless statement: a hook must be valid SQL.
#}
{% macro record_dbt_results(results) -%}
    {%- if execute and target.name != 'ci' and results -%}
        {%- set table = adapter.get_relation(database=target.database, schema='OPS', identifier='DBT_RESULTS') -%}
        {%- if table is not none -%}
            {%- set run_id = env_var('AIRFLOW_RUN_ID', 'manual') -%}
            {%- set rows = [] -%}
            {%- for r in results -%}
                {%- do rows.append(
                    "(" ~ ops_sql_string(run_id)
                    ~ ", " ~ ops_sql_string(invocation_id)
                    ~ ", " ~ ops_sql_string(r.node.unique_id)
                    ~ ", " ~ ops_sql_string(r.node.resource_type)
                    ~ ", " ~ ops_sql_string(r.node.name)
                    ~ ", " ~ ops_sql_string(r.status)
                    ~ ", " ~ (r.failures if r.failures is not none else 'null')
                    ~ ", " ~ (r.execution_time or 0)
                    ~ ", " ~ ops_sql_string((r.message or '') | string | truncate(500, true, ''))
                    ~ ")"
                ) -%}
            {%- endfor -%}
            {%- do run_query(
                "insert into " ~ table
                ~ " (AIRFLOW_RUN_ID, INVOCATION_ID, NODE_ID, RESOURCE_TYPE, NAME, STATUS, FAILURES,"
                ~ " EXECUTION_SECONDS, MESSAGE) values " ~ rows | join(", ")
            ) -%}
            {%- do record_dbt_warnings(results, run_id) -%}
        {%- endif -%}
    {%- endif -%}
    select 'dbt results recorded' as record_dbt_results
{%- endmacro %}


{% macro record_dbt_warnings(results, run_id) -%}
    {%- set audit = adapter.get_relation(database=target.database, schema='OPS', identifier='DBT_WARNINGS_AUDIT') -%}
    {%- if audit is not none -%}
        {%- for r in results -%}
            {%- if r.node.resource_type == 'test' and (r.status | string) == 'warn' -%}
                {%- set kwargs = r.node.test_metadata.kwargs if r.node.test_metadata else {} -%}
                {%- set keys = kwargs.get('key_columns') or [] -%}
                {%- set model = (r.node.attached_node or '').split('.')[-1] -%}
                {%- if keys and r.node.relation_name -%}
                    {%- set pairs = [] -%}
                    {%- set names = [] -%}
                    {%- for k in keys -%}
                        {%- do pairs.append(ops_sql_string(k | upper) ~ ", " ~ k) -%}
                        {%- do names.append(ops_sql_string(k | upper)) -%}
                    {%- endfor -%}
                    {%- set sample = "(select array_agg(object_construct(" ~ pairs | join(', ') ~ ")) from (select "
                        ~ keys | join(', ') ~ " from " ~ r.node.relation_name ~ " limit 20))" -%}
                    {%- set key_list = "array_construct(" ~ names | join(', ') ~ ")" -%}
                {%- else -%}
                    {%- set sample = 'null' -%}
                    {%- set key_list = 'null' -%}
                {%- endif -%}
                {%- do run_query(
                    "insert into " ~ audit
                    ~ " (AIRFLOW_RUN_ID, TEST_NAME, NODE_ID, MODEL, COLUMN_NAME, FAILING_ROWS, KEY_COLUMNS, SAMPLE_KEYS)"
                    ~ " select " ~ ops_sql_string(run_id)
                    ~ ", " ~ ops_sql_string(r.node.name)
                    ~ ", " ~ ops_sql_string(r.node.unique_id)
                    ~ ", " ~ ops_sql_string(model)
                    ~ ", " ~ ops_sql_string(r.node.column_name or '')
                    ~ ", " ~ (r.failures if r.failures is not none else 'null')
                    ~ ", " ~ key_list
                    ~ ", " ~ sample
                ) -%}
            {%- endif -%}
        {%- endfor -%}
    {%- endif -%}
{%- endmacro %}


{# A Snowflake string literal: backslashes and single quotes escaped. #}
{% macro ops_sql_string(value) -%}
    '{{ (value if value is not none else '') | string | replace("\\", "\\\\") | replace("'", "''") }}'
{%- endmacro %}
