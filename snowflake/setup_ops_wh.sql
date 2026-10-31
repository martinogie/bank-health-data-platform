-- BANK_OPS_WH: a warehouse of its own for the GitHub late-run watchdog (Phase 3).
--
-- Why: the watchdog used BANK_DEV_WH, so when dev work used up BANK_DEV_WH_MONITOR the watchdog
-- couldn't check prod either. On its own X-Small warehouse it is covered only by the account cap
-- (ACCOUNT_MONTHLY_CAP), like prod. Cost: one short query on Mondays, about 0.017 credits a run
-- (60-second minimum), about 0.07 credits a month.
--
-- Who can use it: only a new read-only role, OPS_WATCHDOG_ROLE, granted to the CI service user.
-- CI_ROLE (dbt slim CI) and DEV_ROLE get NO usage on it, so dev and CI work can't move onto this
-- warehouse and get around the dev cap. The role can read the run log and nothing else.
--
-- Run once, as ACCOUNTADMIN, after review. Safe to re-run (IF NOT EXISTS / GRANT are idempotent).
-- It creates one warehouse and one role and grants read-only access; it drops nothing and
-- changes no existing warehouse, monitor or role.

USE ROLE ACCOUNTADMIN;

-- 1. The warehouse. No resource monitor of its own: the account-level ACCOUNT_MONTHLY_CAP applies
--    to every warehouse automatically.
CREATE WAREHOUSE IF NOT EXISTS BANK_OPS_WH
  WAREHOUSE_SIZE      = 'XSMALL'
  AUTO_SUSPEND        = 60
  AUTO_RESUME         = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Bank Health ops checks from outside the VM (GitHub late-run watchdog). Under ACCOUNT_MONTHLY_CAP only.';

-- 2. The role: use the warehouse, read the prod run log. Nothing else.
CREATE ROLE IF NOT EXISTS OPS_WATCHDOG_ROLE
  COMMENT = 'GitHub late-run watchdog: read BANK_HEALTH.OPS.PIPELINE_RUNS on BANK_OPS_WH; read-only';
GRANT ROLE OPS_WATCHDOG_ROLE TO ROLE SYSADMIN;   -- so admins can see and manage it

GRANT USAGE  ON WAREHOUSE BANK_OPS_WH                     TO ROLE OPS_WATCHDOG_ROLE;
GRANT USAGE  ON DATABASE  BANK_HEALTH                     TO ROLE OPS_WATCHDOG_ROLE;
GRANT USAGE  ON SCHEMA    BANK_HEALTH.OPS                 TO ROLE OPS_WATCHDOG_ROLE;
GRANT SELECT ON TABLE     BANK_HEALTH.OPS.PIPELINE_RUNS   TO ROLE OPS_WATCHDOG_ROLE;

-- 3. The CI service user may switch to it (the watchdog workflow sets SNOWFLAKE_ROLE to it).
--    Its default role stays CI_ROLE.
GRANT ROLE OPS_WATCHDOG_ROLE TO USER SVC_BANK_CI;

-- 4. Checks (read-only). Expected results in the comments.
SHOW WAREHOUSES LIKE 'BANK_OPS_WH';        -- size X-Small, auto_suspend 60, resource_monitor null
SHOW GRANTS ON WAREHOUSE BANK_OPS_WH;      -- OWNERSHIP ACCOUNTADMIN; USAGE OPS_WATCHDOG_ROLE only
SHOW GRANTS TO ROLE OPS_WATCHDOG_ROLE;     -- USAGE wh/db/schema, SELECT PIPELINE_RUNS; nothing else
SHOW GRANTS TO USER SVC_BANK_CI;           -- CI_ROLE and OPS_WATCHDOG_ROLE
SHOW RESOURCE MONITORS;                    -- unchanged: ACCOUNT_MONTHLY_CAP, BANK_DEV_WH_MONITOR
