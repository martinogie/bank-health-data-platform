#!/bin/bash
# Runs once, when the Postgres volume is first created (docker-entrypoint-initdb.d).
# Creates the mirror's schemas and a read-only login role "viewer".
# The viewer password comes from POSTGRES_VIEWER_PASSWORD (.env); it is never printed.
set -euo pipefail
: "${POSTGRES_VIEWER_PASSWORD:?POSTGRES_VIEWER_PASSWORD must be set in .env}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v viewer_pw="$POSTGRES_VIEWER_PASSWORD" <<'SQL'
\set QUIET on
create schema if not exists bronze;           comment on schema bronze is 'Databricks bronze: raw files as Delta, values as delivered (text)';
create schema if not exists silver;           comment on schema silver is 'Databricks silver: typed, de-duplicated, complaints matched to FDIC banks';
create schema if not exists gold;             comment on schema gold is 'Databricks gold: one row per bank per quarter with derived ratios';
create schema if not exists ml;               comment on schema ml is 'Databricks ml: model features/labels, metrics and risk scores';
create schema if not exists snowflake_raw;    comment on schema snowflake_raw is 'Snowflake BANK_HEALTH.RAW: Databricks exports loaded by Airflow';
create schema if not exists snowflake_marts;  comment on schema snowflake_marts is 'Snowflake BANK_HEALTH.MARTS: dbt marts used by Power BI';
create schema if not exists explore;          comment on schema explore is 'Views for browsing and checking the data, plus the mirror log';

-- Read-only role for browsing (pgAdmin's pre-registered server logs in as viewer)
create role viewer login password :'viewer_pw';
alter role viewer set default_transaction_read_only = on;
grant connect on database bank_health to viewer;
grant usage on schema bronze, silver, gold, ml, snowflake_raw, snowflake_marts, explore to viewer;
grant select on all tables in schema bronze, silver, gold, ml, snowflake_raw, snowflake_marts, explore to viewer;
-- Tables and views the mirror creates later (it drops and reloads them) are readable too
alter default privileges in schema bronze, silver, gold, ml, snowflake_raw, snowflake_marts, explore
    grant select on tables to viewer;
SQL
echo "bank_health: schemas and read-only role viewer created"
