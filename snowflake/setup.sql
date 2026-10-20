-- =============================================================================
-- Bank Health Data Platform — one-time Snowflake setup
-- Run this in a Snowsight worksheet as ACCOUNTADMIN, top to bottom.
--
-- Creates:
--   * BANK_WH            XSMALL warehouse that sleeps after 60 s idle
--   * BANK_WH_MONITOR    credit cap so the trial credit can't be burnt by accident
--   * BANK_HEALTH        database with schemas RAW, STAGING, MARTS
--   * BANK_PIPELINE_ROLE role used by Airflow (loading) and dbt (transforming)
--   * SVC_BANK_PIPELINE  service user, key-pair login only (no password)
--
-- Safe to re-run: everything uses IF NOT EXISTS / OR REPLACE on non-data objects.
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- -----------------------------------------------------------------------------
-- 1. Compute: a small warehouse that only costs money while queries run
-- -----------------------------------------------------------------------------
CREATE WAREHOUSE IF NOT EXISTS BANK_WH
  WAREHOUSE_SIZE      = 'XSMALL'
  AUTO_SUSPEND        = 60       -- seconds idle before it pauses
  AUTO_RESUME         = TRUE     -- wakes up automatically on the next query
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Bank Health pipeline: loads, dbt and Power BI refresh';

-- Spending guard: notify at 75 %, suspend at 100 % of 20 credits per month.
-- An XSMALL warehouse uses 1 credit/hour, so 20 credits is far above normal use.
CREATE OR REPLACE RESOURCE MONITOR BANK_WH_MONITOR
  WITH CREDIT_QUOTA = 20
       FREQUENCY = MONTHLY
       START_TIMESTAMP = IMMEDIATELY
  TRIGGERS ON 75 PERCENT DO NOTIFY
           ON 100 PERCENT DO SUSPEND;

ALTER WAREHOUSE BANK_WH SET RESOURCE_MONITOR = BANK_WH_MONITOR;

-- -----------------------------------------------------------------------------
-- 2. Storage: database and the three layers
--    RAW      exact copies of what Databricks produced (loaded by Airflow)
--    STAGING  dbt staging + intermediate models (cleaned, renamed)
--    MARTS    dbt marts that Power BI reads
-- -----------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS BANK_HEALTH COMMENT = 'Bank Health Data Platform';
CREATE SCHEMA   IF NOT EXISTS BANK_HEALTH.RAW;
CREATE SCHEMA   IF NOT EXISTS BANK_HEALTH.STAGING;
CREATE SCHEMA   IF NOT EXISTS BANK_HEALTH.MARTS;

-- -----------------------------------------------------------------------------
-- 3. Role with just the permissions the pipeline needs
-- -----------------------------------------------------------------------------
CREATE ROLE IF NOT EXISTS BANK_PIPELINE_ROLE COMMENT = 'Airflow loads + dbt builds';

GRANT USAGE, OPERATE ON WAREHOUSE BANK_WH TO ROLE BANK_PIPELINE_ROLE;
GRANT USAGE ON DATABASE BANK_HEALTH         TO ROLE BANK_PIPELINE_ROLE;

-- Full control inside the three schemas (create/replace tables and views)
GRANT ALL PRIVILEGES ON SCHEMA BANK_HEALTH.RAW     TO ROLE BANK_PIPELINE_ROLE;
GRANT ALL PRIVILEGES ON SCHEMA BANK_HEALTH.STAGING TO ROLE BANK_PIPELINE_ROLE;
GRANT ALL PRIVILEGES ON SCHEMA BANK_HEALTH.MARTS   TO ROLE BANK_PIPELINE_ROLE;

-- Lets dbt create extra schemas if a model config ever asks for one
GRANT CREATE SCHEMA ON DATABASE BANK_HEALTH TO ROLE BANK_PIPELINE_ROLE;

-- Tables/views created later (by anyone) are readable by the pipeline role
GRANT SELECT ON FUTURE TABLES IN DATABASE BANK_HEALTH TO ROLE BANK_PIPELINE_ROLE;
GRANT SELECT ON FUTURE VIEWS  IN DATABASE BANK_HEALTH TO ROLE BANK_PIPELINE_ROLE;

-- Standard practice: custom roles roll up to SYSADMIN
GRANT ROLE BANK_PIPELINE_ROLE TO ROLE SYSADMIN;

-- Give your own login the role too (handy in worksheets and for Power BI).
-- The name is wrapped in double quotes so it matches exactly, including a
-- lower-case username (unquoted names are upper-cased by Snowflake).
-- IDENTIFIER() accepts a variable but not an expression, so the quotes are
-- added when the variable is set.
SET my_user = '"' || CURRENT_USER() || '"';
GRANT ROLE BANK_PIPELINE_ROLE TO USER IDENTIFIER($my_user);

-- -----------------------------------------------------------------------------
-- 4. Service user for Airflow + dbt (no password, key-pair auth only)
-- -----------------------------------------------------------------------------
CREATE USER IF NOT EXISTS SVC_BANK_PIPELINE
  TYPE              = SERVICE
  DEFAULT_ROLE      = BANK_PIPELINE_ROLE
  DEFAULT_WAREHOUSE = BANK_WH
  DEFAULT_NAMESPACE = BANK_HEALTH.RAW
  COMMENT = 'Airflow + dbt service account (key-pair auth)';

GRANT ROLE BANK_PIPELINE_ROLE TO USER SVC_BANK_PIPELINE;

-- -----------------------------------------------------------------------------
-- 5. Attach the PUBLIC key (do this after generating the key pair)
--    Paste the contents of keys/snowflake_rsa_key.pub between the quotes,
--    WITHOUT the "-----BEGIN PUBLIC KEY-----" / "-----END PUBLIC KEY-----"
--    lines and without line breaks. Never paste the private key (.p8) here.
-- -----------------------------------------------------------------------------
-- ALTER USER SVC_BANK_PIPELINE SET RSA_PUBLIC_KEY = 'MIIBIjANBgkqh...paste here...';

-- -----------------------------------------------------------------------------
-- 6. Checks — run these to confirm everything is in place
-- -----------------------------------------------------------------------------
SHOW WAREHOUSES LIKE 'BANK_WH';
SHOW SCHEMAS IN DATABASE BANK_HEALTH;
SHOW GRANTS TO ROLE BANK_PIPELINE_ROLE;
DESC USER SVC_BANK_PIPELINE;   -- after step 5, RSA_PUBLIC_KEY_FP shows a SHA256 fingerprint
