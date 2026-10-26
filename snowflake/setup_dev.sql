-- =============================================================================
-- Bank Health Data Platform — dev and CI environments in Snowflake
-- Run this in a Snowsight worksheet as ACCOUNTADMIN.
-- Run snowflake/setup.sql first; this script builds on it.
--
-- First run: execute section 0 alone; only continue if it reports
-- ACCOUNT_MONTHLY_CAP found. Then run the rest top to bottom.
--
-- Creates:
--   * BANK_DEV_WH          XSMALL warehouse for dev and CI, sleeps after 60 s idle
--   * BANK_DEV_WH_MONITOR  1 credit/month cap on BANK_DEV_WH, so dev and CI can't use
--                          prod's share of the account cap
--   * DEV_ROLE             full control of BANK_HEALTH_DEV; nothing on BANK_HEALTH
--   * CI_ROLE              creates temporary CI_<run_id> schemas in BANK_HEALTH_DEV;
--                          read-only on BANK_HEALTH (for dbt --defer); never writes prod
--   * BANK_HEALTH_DEV      zero-copy clone of BANK_HEALTH (no extra storage until it changes)
--   * SVC_BANK_DEV         service user for the dev Airflow stack and dev dbt (key pair only)
--   * SVC_BANK_CI          service user for GitHub Actions (key pair only)
--   * BANK_HEALTH.RAW.DBT_ARTIFACTS  stage holding the manifest of the latest successful
--                          prod dbt run, read by CI. Owned by BANK_PIPELINE_ROLE.
-- Removes:
--   * BANK_WH_MONITOR      redundant once the account-level ACCOUNT_MONTHLY_CAP exists
--
-- Step 0 checks for ACCOUNT_MONTHLY_CAP first. If it is missing or not set at account
-- level, the script stops there with an error and changes nothing.
--
-- Safe to re-run: IF NOT EXISTS everywhere except the monitor (OR REPLACE) and the
-- grants (re-applying a grant is a no-op).
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- -----------------------------------------------------------------------------
-- 0. Spending guard: ACCOUNT_MONTHLY_CAP must exist at account level.
--    Only then is BANK_WH_MONITOR (20 credits on BANK_WH) dropped: the 5-credit
--    account cap always stops spending first, so the old monitor can never fire.
-- -----------------------------------------------------------------------------
EXECUTE IMMEDIATE $$
DECLARE
  account_caps INTEGER DEFAULT 0;
  no_account_cap EXCEPTION (-20001, 'ACCOUNT_MONTHLY_CAP is missing or not set at account level. Stopped before any change. Run section 1 of setup.sql (creates it and sets it on the account), then re-run.');
BEGIN
  SHOW RESOURCE MONITORS LIKE 'ACCOUNT_MONTHLY_CAP';
  SELECT COUNT(*) INTO :account_caps
    FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))
   WHERE "level" = 'ACCOUNT';
  IF (account_caps = 0) THEN
    RAISE no_account_cap;
  END IF;

  ALTER WAREHOUSE IF EXISTS BANK_WH UNSET RESOURCE_MONITOR;
  DROP RESOURCE MONITOR IF EXISTS BANK_WH_MONITOR;
  RETURN 'ACCOUNT_MONTHLY_CAP found at account level; BANK_WH_MONITOR removed';
END;
$$;

-- -----------------------------------------------------------------------------
-- 1. Compute for dev and CI, with its own small cap
-- -----------------------------------------------------------------------------
CREATE WAREHOUSE IF NOT EXISTS BANK_DEV_WH
  WAREHOUSE_SIZE      = 'XSMALL'
  AUTO_SUSPEND        = 60
  AUTO_RESUME         = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Bank Health dev and CI (dbt slim CI, dev pipeline runs)';

CREATE OR REPLACE RESOURCE MONITOR BANK_DEV_WH_MONITOR
  WITH CREDIT_QUOTA = 1
       FREQUENCY = MONTHLY
       START_TIMESTAMP = IMMEDIATELY
  TRIGGERS ON 75 PERCENT DO NOTIFY
           ON 100 PERCENT DO SUSPEND;

ALTER WAREHOUSE BANK_DEV_WH SET RESOURCE_MONITOR = BANK_DEV_WH_MONITOR;
-- ACCOUNT_MONTHLY_CAP covers BANK_DEV_WH automatically (account-level monitors apply
-- to every warehouse); a warehouse can additionally have one warehouse-level monitor.

-- -----------------------------------------------------------------------------
-- 2. Roles. Neither is granted BANK_PIPELINE_ROLE, so neither inherits prod writes.
-- -----------------------------------------------------------------------------
CREATE ROLE IF NOT EXISTS DEV_ROLE COMMENT = 'Dev pipeline + dbt dev: owns BANK_HEALTH_DEV only';
CREATE ROLE IF NOT EXISTS CI_ROLE  COMMENT = 'GitHub Actions dbt slim CI: CI_ schemas in BANK_HEALTH_DEV, read-only on BANK_HEALTH';

GRANT ROLE DEV_ROLE TO ROLE SYSADMIN;
GRANT ROLE CI_ROLE  TO ROLE SYSADMIN;

GRANT USAGE, OPERATE ON WAREHOUSE BANK_DEV_WH TO ROLE DEV_ROLE;
GRANT USAGE          ON WAREHOUSE BANK_DEV_WH TO ROLE CI_ROLE;

-- Your own login gets DEV_ROLE too (for worksheets). Same quoting trick as setup.sql.
SET my_user = '"' || CURRENT_USER() || '"';
GRANT ROLE DEV_ROLE TO USER IDENTIFIER($my_user);

-- -----------------------------------------------------------------------------
-- 3. BANK_HEALTH_DEV: zero-copy clone of prod
-- -----------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS BANK_HEALTH_DEV CLONE BANK_HEALTH
  COMMENT = 'Bank Health dev: zero-copy clone of BANK_HEALTH. Refresh: see the end of setup_dev.sql';

-- 3b. Ownership and grants on the clone. RE-RUN THIS BLOCK AFTER EVERY REFRESH
--     (not while a CI job is running: it would also take over that job's CI_ schema).
--     A clone keeps the source's grants on its objects, so prod roles
--     (BANK_PIPELINE_ROLE, BANK_REPORTER_ROLE) would still have access to it.
--     REVOKE CURRENT GRANTS removes those; DEV_ROLE becomes the only owner.
GRANT OWNERSHIP ON DATABASE BANK_HEALTH_DEV                      TO ROLE DEV_ROLE REVOKE CURRENT GRANTS;
GRANT OWNERSHIP ON ALL SCHEMAS      IN DATABASE BANK_HEALTH_DEV  TO ROLE DEV_ROLE REVOKE CURRENT GRANTS;
GRANT OWNERSHIP ON ALL TABLES       IN DATABASE BANK_HEALTH_DEV  TO ROLE DEV_ROLE REVOKE CURRENT GRANTS;
GRANT OWNERSHIP ON ALL VIEWS        IN DATABASE BANK_HEALTH_DEV  TO ROLE DEV_ROLE REVOKE CURRENT GRANTS;
GRANT OWNERSHIP ON ALL STAGES       IN DATABASE BANK_HEALTH_DEV  TO ROLE DEV_ROLE REVOKE CURRENT GRANTS;
GRANT OWNERSHIP ON ALL FILE FORMATS IN DATABASE BANK_HEALTH_DEV  TO ROLE DEV_ROLE REVOKE CURRENT GRANTS;
GRANT OWNERSHIP ON ALL SEQUENCES    IN DATABASE BANK_HEALTH_DEV  TO ROLE DEV_ROLE REVOKE CURRENT GRANTS;

-- Future grants the clone may have copied from prod (setup.sql grants them database-wide,
-- powerbi_user.sql on MARTS). Revoking one that doesn't exist is a harmless no-op.
REVOKE SELECT ON FUTURE TABLES IN DATABASE BANK_HEALTH_DEV     FROM ROLE BANK_PIPELINE_ROLE;
REVOKE SELECT ON FUTURE VIEWS  IN DATABASE BANK_HEALTH_DEV     FROM ROLE BANK_PIPELINE_ROLE;
REVOKE SELECT ON FUTURE TABLES IN SCHEMA BANK_HEALTH_DEV.MARTS FROM ROLE BANK_REPORTER_ROLE;
REVOKE SELECT ON FUTURE VIEWS  IN SCHEMA BANK_HEALTH_DEV.MARTS FROM ROLE BANK_REPORTER_ROLE;

-- CI creates (and drops) its own CI_<run_id> schema; as the schema's owner it can
-- build models in it. It gets nothing else in BANK_HEALTH_DEV.
GRANT USAGE         ON DATABASE BANK_HEALTH_DEV TO ROLE CI_ROLE;
GRANT CREATE SCHEMA ON DATABASE BANK_HEALTH_DEV TO ROLE CI_ROLE;

-- NOTE: the cloned dbt views in STAGING still point at BANK_HEALTH (dbt writes fully
-- qualified names). After a clone or refresh, rebuild dev from the dev folder with:
--   cd dbt && dbt build --target dev

-- -----------------------------------------------------------------------------
-- 4. CI: read-only on prod, so `dbt build --defer` can read the unchanged prod models
--    and prod RAW sources. SELECT only: no INSERT, CREATE or OWNERSHIP.
-- -----------------------------------------------------------------------------
GRANT USAGE ON DATABASE BANK_HEALTH         TO ROLE CI_ROLE;
GRANT USAGE ON SCHEMA   BANK_HEALTH.RAW     TO ROLE CI_ROLE;
GRANT USAGE ON SCHEMA   BANK_HEALTH.STAGING TO ROLE CI_ROLE;
GRANT USAGE ON SCHEMA   BANK_HEALTH.MARTS   TO ROLE CI_ROLE;

GRANT SELECT ON ALL TABLES IN SCHEMA BANK_HEALTH.RAW     TO ROLE CI_ROLE;
GRANT SELECT ON ALL VIEWS  IN SCHEMA BANK_HEALTH.RAW     TO ROLE CI_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA BANK_HEALTH.STAGING TO ROLE CI_ROLE;
GRANT SELECT ON ALL VIEWS  IN SCHEMA BANK_HEALTH.STAGING TO ROLE CI_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA BANK_HEALTH.MARTS   TO ROLE CI_ROLE;
GRANT SELECT ON ALL VIEWS  IN SCHEMA BANK_HEALTH.MARTS   TO ROLE CI_ROLE;

-- Future objects (dbt and the loader recreate tables every week).
-- RAW and STAGING: database-level future grants, like setup.sql uses. Adding
-- schema-level ones there would silently override setup.sql's database-level grants.
-- MARTS already has schema-level future grants (powerbi_user.sql), which override
-- database-level ones, so CI needs a schema-level grant there.
GRANT SELECT ON FUTURE TABLES IN DATABASE BANK_HEALTH       TO ROLE CI_ROLE;
GRANT SELECT ON FUTURE VIEWS  IN DATABASE BANK_HEALTH       TO ROLE CI_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA BANK_HEALTH.MARTS   TO ROLE CI_ROLE;
GRANT SELECT ON FUTURE VIEWS  IN SCHEMA BANK_HEALTH.MARTS   TO ROLE CI_ROLE;

-- -----------------------------------------------------------------------------
-- 5. Stage for the prod dbt manifest (dbt slim CI compares against it).
--    Created as BANK_PIPELINE_ROLE so prod owns it; CI may only read it.
--    Prod Airflow uploads manifest.json here after each successful dbt build.
-- -----------------------------------------------------------------------------
USE ROLE BANK_PIPELINE_ROLE;
CREATE STAGE IF NOT EXISTS BANK_HEALTH.RAW.DBT_ARTIFACTS
  COMMENT = 'manifest.json from the latest successful prod dbt build (read by CI)';
GRANT READ ON STAGE BANK_HEALTH.RAW.DBT_ARTIFACTS TO ROLE CI_ROLE;
USE ROLE ACCOUNTADMIN;

-- -----------------------------------------------------------------------------
-- 6. Service users (no password, key pair only, no secondary roles)
-- -----------------------------------------------------------------------------
CREATE USER IF NOT EXISTS SVC_BANK_DEV
  TYPE                    = SERVICE
  DEFAULT_ROLE            = DEV_ROLE
  DEFAULT_WAREHOUSE       = BANK_DEV_WH
  DEFAULT_NAMESPACE       = BANK_HEALTH_DEV.RAW
  DEFAULT_SECONDARY_ROLES = ()
  COMMENT = 'Dev Airflow stack + dbt dev (key-pair auth)';
GRANT ROLE DEV_ROLE TO USER SVC_BANK_DEV;

CREATE USER IF NOT EXISTS SVC_BANK_CI
  TYPE                    = SERVICE
  DEFAULT_ROLE            = CI_ROLE
  DEFAULT_WAREHOUSE       = BANK_DEV_WH
  DEFAULT_NAMESPACE       = BANK_HEALTH_DEV
  DEFAULT_SECONDARY_ROLES = ()
  COMMENT = 'GitHub Actions dbt slim CI (key-pair auth)';
GRANT ROLE CI_ROLE TO USER SVC_BANK_CI;

-- Attach the PUBLIC keys (one line each, without the BEGIN/END lines, no line breaks).
-- Never paste a private key (.p8) here.
-- ALTER USER SVC_BANK_DEV SET RSA_PUBLIC_KEY = 'MIIBIjANBgkqh...paste keys/snowflake_dev_rsa_key.pub...';
-- ALTER USER SVC_BANK_CI  SET RSA_PUBLIC_KEY = 'MIIBIjANBgkqh...paste keys/snowflake_ci_rsa_key.pub...';

-- -----------------------------------------------------------------------------
-- 7. Checks
-- -----------------------------------------------------------------------------
SHOW RESOURCE MONITORS;                 -- ACCOUNT_MONTHLY_CAP (ACCOUNT) + BANK_DEV_WH_MONITOR; no BANK_WH_MONITOR
SHOW WAREHOUSES LIKE 'BANK%';           -- BANK_DEV_WH: resource_monitor = BANK_DEV_WH_MONITOR
SHOW GRANTS TO ROLE DEV_ROLE;           -- nothing on BANK_HEALTH
SHOW GRANTS TO ROLE CI_ROLE;            -- on BANK_HEALTH: USAGE and SELECT only
SHOW GRANTS ON DATABASE BANK_HEALTH_DEV;-- owner DEV_ROLE; CI_ROLE USAGE + CREATE SCHEMA
DESC USER SVC_BANK_DEV;                 -- after the ALTER USER lines: RSA_PUBLIC_KEY_FP is set
DESC USER SVC_BANK_CI;

-- Future grants on the clone: neither BANK_PIPELINE_ROLE nor BANK_REPORTER_ROLE may appear.
SHOW FUTURE GRANTS IN DATABASE BANK_HEALTH_DEV;
SHOW FUTURE GRANTS IN SCHEMA BANK_HEALTH_DEV.MARTS;

-- Every object in BANK_HEALTH_DEV not owned by DEV_ROLE. Expect no rows.
-- (A CI_<run_id> schema owned by CI_ROLE appears only while a CI job is running.)
SELECT 'DATABASE' AS object_type, database_name AS object_name, database_owner AS owner
  FROM BANK_HEALTH_DEV.INFORMATION_SCHEMA.DATABASES
 WHERE database_name = 'BANK_HEALTH_DEV' AND COALESCE(database_owner, '') <> 'DEV_ROLE'
UNION ALL
SELECT 'SCHEMA', schema_name, schema_owner
  FROM BANK_HEALTH_DEV.INFORMATION_SCHEMA.SCHEMATA
 WHERE schema_name <> 'INFORMATION_SCHEMA' AND COALESCE(schema_owner, '') <> 'DEV_ROLE'
UNION ALL
SELECT table_type, table_schema || '.' || table_name, table_owner     -- tables and views
  FROM BANK_HEALTH_DEV.INFORMATION_SCHEMA.TABLES
 WHERE table_schema <> 'INFORMATION_SCHEMA' AND COALESCE(table_owner, '') <> 'DEV_ROLE'
UNION ALL
SELECT 'STAGE', stage_schema || '.' || stage_name, stage_owner
  FROM BANK_HEALTH_DEV.INFORMATION_SCHEMA.STAGES
 WHERE COALESCE(stage_owner, '') <> 'DEV_ROLE'
UNION ALL
SELECT 'FILE FORMAT', file_format_schema || '.' || file_format_name, file_format_owner
  FROM BANK_HEALTH_DEV.INFORMATION_SCHEMA.FILE_FORMATS
 WHERE COALESCE(file_format_owner, '') <> 'DEV_ROLE'
UNION ALL
SELECT 'SEQUENCE', sequence_schema || '.' || sequence_name, sequence_owner
  FROM BANK_HEALTH_DEV.INFORMATION_SCHEMA.SEQUENCES
 WHERE COALESCE(sequence_owner, '') <> 'DEV_ROLE';

-- The automated proof that dev and CI cannot write prod is
-- tests/integration/test_environment_safety.py (logs in as each service user,
-- tries CREATE / INSERT / CREATE SCHEMA in BANK_HEALTH, and expects each to fail).

-- =============================================================================
-- REFRESH BANK_HEALTH_DEV from prod (run by hand when dev data is too old).
-- Replaces the whole dev database, including any CI_ schemas left behind.
--   USE ROLE ACCOUNTADMIN;
--   CREATE OR REPLACE DATABASE BANK_HEALTH_DEV CLONE BANK_HEALTH;
--   -- then re-run section 3b (ownership + CI grants), then from the dev folder:
--   --   cd dbt && dbt build --target dev
-- =============================================================================
