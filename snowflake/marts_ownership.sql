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
-- Also (step 3): database-level FUTURE grants for Power BI's reporter role, as a safety net (option
-- C, agreed 1 Nov 2026). Each publish already grants SELECT on every mart before the swap (option A),
-- but schema-level future grants can't be re-applied after a swap without MANAGE GRANTS, so they
-- disappear from MARTS at the first swap. Database-level future grants make any table or view dbt
-- creates in BANK_HEALTH readable by the reporter role from the moment it exists. The reporter role
-- has USAGE only on MARTS (and on last week's copy in MARTS_NEXT), so it still can't read RAW,
-- STAGING or OPS. CI_ROLE already has the same database-level future grants (setup_dev.sql).
-- Note: Snowflake applies a schema's own future grants instead of database-level ones, so these take
-- effect in MARTS after its first swap (the new MARTS has no schema-level future grants).
--
-- Run once, as ACCOUNTADMIN, after review, on the day PR 4 is deployed (before its deploy).
-- Safe to re-run.

USE ROLE ACCOUNTADMIN;

-- 0. Before: record the current owner and grants (compare with step 2).
SHOW GRANTS ON SCHEMA BANK_HEALTH.MARTS;          -- OWNERSHIP ACCOUNTADMIN; USAGE BANK_REPORTER_ROLE, CI_ROLE; ALL BANK_PIPELINE_ROLE
SHOW FUTURE GRANTS IN SCHEMA BANK_HEALTH.MARTS;   -- SELECT on future TABLE/VIEW for BANK_REPORTER_ROLE and CI_ROLE

-- 1. Hand the schema to the pipeline role, keeping every current grant.
GRANT OWNERSHIP ON SCHEMA BANK_HEALTH.MARTS TO ROLE BANK_PIPELINE_ROLE COPY CURRENT GRANTS;

-- 2. After: the same grants as step 0, except OWNERSHIP now BANK_PIPELINE_ROLE.
SHOW GRANTS ON SCHEMA BANK_HEALTH.MARTS;
SHOW FUTURE GRANTS IN SCHEMA BANK_HEALTH.MARTS;

-- 3. Database-level future grants for the reporter role (option C).
SHOW FUTURE GRANTS IN DATABASE BANK_HEALTH;       -- before: CI_ROLE (TABLE, VIEW) and BANK_PIPELINE_ROLE
GRANT SELECT ON FUTURE TABLES IN DATABASE BANK_HEALTH TO ROLE BANK_REPORTER_ROLE;
GRANT SELECT ON FUTURE VIEWS  IN DATABASE BANK_HEALTH TO ROLE BANK_REPORTER_ROLE;
SHOW FUTURE GRANTS IN DATABASE BANK_HEALTH;       -- after: also SELECT on TABLE and VIEW for BANK_REPORTER_ROLE

-- Rollback (if ever needed):
--   GRANT OWNERSHIP ON SCHEMA BANK_HEALTH.MARTS TO ROLE ACCOUNTADMIN COPY CURRENT GRANTS;
--   REVOKE SELECT ON FUTURE TABLES IN DATABASE BANK_HEALTH FROM ROLE BANK_REPORTER_ROLE;
--   REVOKE SELECT ON FUTURE VIEWS  IN DATABASE BANK_HEALTH FROM ROLE BANK_REPORTER_ROLE;
