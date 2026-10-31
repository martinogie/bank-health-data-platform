-- Write, audit, publish (Phase 3, PR 4): let the pipeline swap the marts.
--
-- Why: the pipeline now builds and tests the marts in a clone, BANK_HEALTH.MARTS_NEXT, and swaps it
-- with BANK_HEALTH.MARTS only when every critical test passed (ALTER SCHEMA ... SWAP WITH ...).
-- Snowflake lets a role swap two schemas only if it owns both. The pipeline role already creates
-- MARTS_NEXT itself (it has CREATE SCHEMA on the database); this hands it ownership of MARTS too.
--
-- What it changes: the owner of one schema, BANK_HEALTH.MARTS, from ACCOUNTADMIN to
-- BANK_PIPELINE_ROLE. COPY CURRENT GRANTS keeps every existing grant on the schema exactly as it is
-- (BANK_REPORTER_ROLE and CI_ROLE keep USAGE and their future-table grants), so Power BI and CI see
-- no change. The pipeline role already had ALL PRIVILEGES in the schema and owns the tables in it.
-- Nothing is dropped. Dev needs nothing: DEV_ROLE already owns BANK_HEALTH_DEV and its schemas.
--
-- Run once, as ACCOUNTADMIN, after review, before PR 4 is deployed. Safe to re-run.

USE ROLE ACCOUNTADMIN;

-- 0. Before: record the current owner and grants (compare with step 2).
SHOW GRANTS ON SCHEMA BANK_HEALTH.MARTS;          -- OWNERSHIP ACCOUNTADMIN; USAGE BANK_REPORTER_ROLE, CI_ROLE; ALL BANK_PIPELINE_ROLE
SHOW FUTURE GRANTS IN SCHEMA BANK_HEALTH.MARTS;   -- SELECT on future TABLE/VIEW for BANK_REPORTER_ROLE and CI_ROLE

-- 1. Hand the schema to the pipeline role, keeping every current grant.
GRANT OWNERSHIP ON SCHEMA BANK_HEALTH.MARTS TO ROLE BANK_PIPELINE_ROLE COPY CURRENT GRANTS;

-- 2. After: the same grants as step 0, except OWNERSHIP now BANK_PIPELINE_ROLE.
SHOW GRANTS ON SCHEMA BANK_HEALTH.MARTS;
SHOW FUTURE GRANTS IN SCHEMA BANK_HEALTH.MARTS;

-- Rollback (if ever needed): GRANT OWNERSHIP ON SCHEMA BANK_HEALTH.MARTS TO ROLE ACCOUNTADMIN COPY CURRENT GRANTS;
