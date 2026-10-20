-- =============================================================================
-- Read-only Power BI access to BANK_HEALTH.MARTS (run once as ACCOUNTADMIN)
--
-- Why: Snowflake requires MFA for human password logins, and Power BI's password
-- login is being deprecated. Power BI Desktop's Snowflake connector supports
-- key-pair login, so Power BI gets its own service user with its own key pair.
--
-- Creates:
--   * BANK_REPORTER_ROLE  can only USE the warehouse and SELECT from MARTS
--   * SVC_POWERBI         service user, key-pair login only (no password)
-- Safe to re-run.
-- =============================================================================

USE ROLE ACCOUNTADMIN;

CREATE ROLE IF NOT EXISTS BANK_REPORTER_ROLE COMMENT = 'Read-only access to BANK_HEALTH.MARTS for Power BI';

GRANT USAGE ON WAREHOUSE BANK_WH                TO ROLE BANK_REPORTER_ROLE;
GRANT USAGE ON DATABASE BANK_HEALTH             TO ROLE BANK_REPORTER_ROLE;
GRANT USAGE ON SCHEMA BANK_HEALTH.MARTS         TO ROLE BANK_REPORTER_ROLE;

-- Existing mart tables/views...
GRANT SELECT ON ALL TABLES IN SCHEMA BANK_HEALTH.MARTS TO ROLE BANK_REPORTER_ROLE;
GRANT SELECT ON ALL VIEWS  IN SCHEMA BANK_HEALTH.MARTS TO ROLE BANK_REPORTER_ROLE;
-- ...and the ones dbt re-creates on every run
GRANT SELECT ON FUTURE TABLES IN SCHEMA BANK_HEALTH.MARTS TO ROLE BANK_REPORTER_ROLE;
GRANT SELECT ON FUTURE VIEWS  IN SCHEMA BANK_HEALTH.MARTS TO ROLE BANK_REPORTER_ROLE;
-- Note: schema-level future grants take precedence over the database-level ones in
-- setup.sql for this schema. BANK_PIPELINE_ROLE is unaffected: it creates and owns
-- the MARTS tables.

GRANT ROLE BANK_REPORTER_ROLE TO ROLE SYSADMIN;

CREATE USER IF NOT EXISTS SVC_POWERBI
  TYPE              = SERVICE
  DEFAULT_ROLE      = BANK_REPORTER_ROLE
  DEFAULT_WAREHOUSE = BANK_WH
  DEFAULT_NAMESPACE = BANK_HEALTH.MARTS
  COMMENT = 'Power BI Desktop import refresh (key-pair auth, read-only)';

GRANT ROLE BANK_REPORTER_ROLE TO USER SVC_POWERBI;

-- Attach the PUBLIC key of a NEW key pair made for Power BI (keys/powerbi_rsa_key.pub),
-- one line, without the BEGIN/END lines. Never paste the private key here.
-- ALTER USER SVC_POWERBI SET RSA_PUBLIC_KEY = 'MIIBIjANBgkqh...paste here...';

-- Checks
SHOW GRANTS TO ROLE BANK_REPORTER_ROLE;
DESC USER SVC_POWERBI;   -- after the ALTER, RSA_PUBLIC_KEY_FP shows a fingerprint
