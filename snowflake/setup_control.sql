-- Phase 4, PR 10: move the one-scheduler guard into its own schema, BANK_HEALTH.CONTROL.
-- Run by Martins in a worksheet as ACCOUNTADMIN, BEFORE PR 10 is deployed (the new code reads
-- CONTROL.PROD_SCHEDULER and stops a prod run if it can't: fail safe).
--
-- Why: OPS.PROD_SCHEDULER is owned by ACCOUNTADMIN, but it sits in OPS, a schema the pipeline role
-- owns, and a schema owner can drop or replace what's inside. CONTROL is owned by ACCOUNTADMIN, with
-- managed access, so the pipeline role can only read the row that restrains it.
--
-- The old table stays until the new one is verified in prod; dropping it (section 3) is a separate
-- step, only after Martins' OK.

USE ROLE ACCOUNTADMIN;
USE WAREHOUSE BANK_WH;   -- the copy needs a running warehouse (a few seconds of XSMALL)

-- 1. The schema and the table (a copy of today's one row, same columns and values)
CREATE SCHEMA IF NOT EXISTS BANK_HEALTH.CONTROL WITH MANAGED ACCESS
  COMMENT = 'Rules that restrain the pipeline (owned by ACCOUNTADMIN; the pipeline role can only read)';
CREATE TABLE IF NOT EXISTS BANK_HEALTH.CONTROL.PROD_SCHEDULER (
  HOST_ID   STRING        NOT NULL,
  SET_AT    TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP(),
  SET_BY    STRING        DEFAULT CURRENT_USER(),
  NOTE      STRING
);
INSERT INTO BANK_HEALTH.CONTROL.PROD_SCHEDULER (HOST_ID, SET_AT, SET_BY, NOTE)
  SELECT HOST_ID, SET_AT, SET_BY, NOTE FROM BANK_HEALTH.OPS.PROD_SCHEDULER
  WHERE NOT EXISTS (SELECT 1 FROM BANK_HEALTH.CONTROL.PROD_SCHEDULER);

-- 2. Read-only access for the pipeline (the guard runs as BANK_PIPELINE_ROLE)
GRANT USAGE  ON SCHEMA BANK_HEALTH.CONTROL                TO ROLE BANK_PIPELINE_ROLE;
GRANT SELECT ON TABLE  BANK_HEALTH.CONTROL.PROD_SCHEDULER TO ROLE BANK_PIPELINE_ROLE;

-- Checks
SHOW SCHEMAS LIKE 'CONTROL' IN DATABASE BANK_HEALTH;          -- owner ACCOUNTADMIN, options MANAGED ACCESS
SHOW GRANTS ON TABLE BANK_HEALTH.CONTROL.PROD_SCHEDULER;      -- OWNERSHIP ACCOUNTADMIN; BANK_PIPELINE_ROLE: SELECT only
SELECT * FROM BANK_HEALTH.CONTROL.PROD_SCHEDULER;             -- one row: oracle-vm (same as OPS)
SELECT (SELECT HOST_ID FROM BANK_HEALTH.OPS.PROD_SCHEDULER)
     = (SELECT HOST_ID FROM BANK_HEALTH.CONTROL.PROD_SCHEDULER) AS SAME_HOST;   -- TRUE

-- 3. LATER, only after PR 10 is deployed, check_environment has passed against CONTROL in prod,
--    and Martins has said OK. Not part of the first run of this file.
-- DROP TABLE BANK_HEALTH.OPS.PROD_SCHEDULER;
