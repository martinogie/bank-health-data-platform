-- =============================================================================
-- Bank Health Data Platform — the OPS schema: run log, dbt results, warnings audit,
-- the one-scheduler guard and the health view. Phase 2 (findings O-03, D-05, C-02).
-- Run in a Snowsight worksheet as ACCOUNTADMIN, AFTER setup.sql and setup_dev.sql.
-- Run it BEFORE merging the pull request that adds the run log (the DAG expects these objects).
--
-- Creates, in both BANK_HEALTH (prod) and BANK_HEALTH_DEV (dev):
--   OPS.PIPELINE_RUNS        one row per DAG run (written by the DAG's last task, record_run)
--   OPS.DBT_RESULTS          one row per dbt model/test result (dbt on-run-end hook)
--   OPS.DBT_WARNINGS_AUDIT   one row per warning test per run, with a sample of row keys
--   OPS.HEALTH_RUNS_LAST_12  view for the health page: the last 12 runs
-- And in prod only:
--   OPS.PROD_SCHEDULER       the one machine allowed to run the prod schedule (row: 'pc')
--
-- Pattern from Phase 1: prod objects are owned by BANK_PIPELINE_ROLE, dev objects by DEV_ROLE.
-- CI_ROLE may read prod OPS (the GitHub late-run watchdog), never write it.
-- Safe to re-run: IF NOT EXISTS for tables, OR REPLACE only for the views.
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- -----------------------------------------------------------------------------
-- 0. NEEDS YOUR APPROVAL FIRST (you approved this grant for the prod pipeline role only):
--    lets record_run read warehouse metering (credits per run). Read-only, account-wide
--    usage visibility. Not granted to CI_ROLE or DEV_ROLE (dev runs record no credits).
-- -----------------------------------------------------------------------------
GRANT MONITOR USAGE ON ACCOUNT TO ROLE BANK_PIPELINE_ROLE;

-- -----------------------------------------------------------------------------
-- 1. PROD: BANK_HEALTH.OPS, owned by the pipeline role
-- -----------------------------------------------------------------------------
USE ROLE BANK_PIPELINE_ROLE;
CREATE SCHEMA IF NOT EXISTS BANK_HEALTH.OPS
  COMMENT = 'Pipeline operations: run log, dbt results, warnings audit, scheduler guard, health view';
USE SCHEMA BANK_HEALTH.OPS;

CREATE TABLE IF NOT EXISTS PIPELINE_RUNS (
  RUN_ID               STRING        NOT NULL,  -- Airflow run id
  ENVIRONMENT          STRING        NOT NULL,  -- prod / dev
  HOST_ID              STRING,                  -- PROD_HOST_ID of the machine that ran it
  DAG_ID               STRING        NOT NULL,
  STARTED_AT           TIMESTAMP_TZ,
  ENDED_AT             TIMESTAMP_TZ,
  DURATION_SECONDS     NUMBER,
  STATE                STRING        NOT NULL,  -- success / failed
  FAILED_TASKS         STRING,                  -- comma-separated task ids, NULL if none
  BRONZE_ROWS          NUMBER,                  -- Databricks bronze, all tables
  SILVER_ROWS          NUMBER,                  -- Databricks silver, all tables
  GOLD_ROWS            NUMBER,                  -- Databricks gold.bank_quarter
  MARTS_ROWS           NUMBER,                  -- Snowflake MARTS, all tables
  SCORED_BANKS         NUMBER,                  -- rows in MARTS.MART_EARLY_WARNING
  ROWS_DETAIL          VARIANT,                 -- per-table counts behind the totals
  DBT_MODELS_OK        NUMBER,
  DBT_MODELS_ERROR     NUMBER,
  DBT_TESTS_PASS       NUMBER,
  DBT_TESTS_WARN       NUMBER,
  DBT_TESTS_FAIL       NUMBER,                  -- fail + error
  DQ_WARNINGS          NUMBER,                  -- data-quality warnings in the Databricks notebooks
  CREDITS_ESTIMATE     FLOAT,                   -- BANK_WH metering over the run window, at run end
  CREDITS_FINAL        FLOAT,                   -- the same, recomputed by the next run once complete
  MODEL_NAME           STRING,
  MODEL_VERSION        STRING,
  MODEL_ALIAS          STRING,
  LATEST_FDIC_QUARTER  DATE,
  VOLUME_ANOMALIES     VARIANT,                 -- what check_volumes flagged, if anything
  SLO_MET              BOOLEAN,                 -- succeeded by Monday 12:00 America/Chicago
  RECORDED_AT          TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS DBT_RESULTS (
  AIRFLOW_RUN_ID     STRING,
  INVOCATION_ID      STRING,
  NODE_ID            STRING,
  RESOURCE_TYPE      STRING,       -- model / test / ...
  NAME               STRING,
  STATUS             STRING,       -- success / error / pass / warn / fail / skipped
  FAILURES           NUMBER,       -- rows a test found
  EXECUTION_SECONDS  FLOAT,
  MESSAGE            STRING,
  RECORDED_AT        TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS DBT_WARNINGS_AUDIT (
  AIRFLOW_RUN_ID   STRING,
  TEST_NAME        STRING,
  NODE_ID          STRING,
  MODEL            STRING,
  COLUMN_NAME      STRING,
  FAILING_ROWS     NUMBER,
  KEY_COLUMNS      ARRAY,
  SAMPLE_KEYS      VARIANT,          -- up to 20 rows of key columns, e.g. [{"CERT":..,"REPORT_DATE":..}]
  RECORDED_AT      TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
);

-- The one machine allowed to run the prod schedule. check_environment stops a prod run on
-- any other machine. Cutover changes this one row (docs/runbooks/cutover_and_rollback.md).
CREATE TABLE IF NOT EXISTS PROD_SCHEDULER (
  HOST_ID   STRING        NOT NULL,
  SET_AT    TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP(),
  SET_BY    STRING        DEFAULT CURRENT_USER(),
  NOTE      STRING
);
INSERT INTO PROD_SCHEDULER (HOST_ID, NOTE)
  SELECT 'pc', 'initial: the PC runs prod until cutover'
  WHERE NOT EXISTS (SELECT 1 FROM PROD_SCHEDULER);

-- Health page data (Step 8): the last 12 runs. Contract: docs/health_page_contract.md.
CREATE OR REPLACE VIEW HEALTH_RUNS_LAST_12 AS
WITH runs AS (
  SELECT r.*,
         COALESCE(r.CREDITS_FINAL, r.CREDITS_ESTIMATE) AS CREDITS,
         w.WARNED_ROWS
  FROM PIPELINE_RUNS r
  LEFT JOIN (SELECT AIRFLOW_RUN_ID, SUM(FAILING_ROWS) AS WARNED_ROWS
             FROM DBT_WARNINGS_AUDIT GROUP BY AIRFLOW_RUN_ID) w
    ON w.AIRFLOW_RUN_ID = r.RUN_ID
),
ranked AS (
  SELECT runs.*,
         AVG(CREDITS) OVER (ORDER BY STARTED_AT ROWS BETWEEN 3 PRECEDING AND CURRENT ROW) AS CREDITS_AVG_LAST_4,
         ROW_NUMBER() OVER (ORDER BY STARTED_AT DESC) AS RUN_RANK
  FROM runs
)
SELECT RUN_RANK, RUN_ID, ENVIRONMENT, HOST_ID, STARTED_AT, ENDED_AT,
       ROUND(DURATION_SECONDS / 60, 1)   AS DURATION_MINUTES,
       STATE, FAILED_TASKS,
       BRONZE_ROWS, SILVER_ROWS, GOLD_ROWS, MARTS_ROWS, SCORED_BANKS,
       DBT_MODELS_OK, DBT_MODELS_ERROR, DBT_TESTS_PASS, DBT_TESTS_WARN, DBT_TESTS_FAIL,
       COALESCE(WARNED_ROWS, 0)          AS WARNED_ROWS,
       DQ_WARNINGS,
       ROUND(CREDITS, 3)                 AS CREDITS,
       CREDITS_FINAL IS NOT NULL         AS CREDITS_ARE_FINAL,
       ROUND(CREDITS_AVG_LAST_4, 3)      AS CREDITS_AVG_LAST_4,
       LATEST_FDIC_QUARTER, SLO_MET, VOLUME_ANOMALIES IS NOT NULL AS HAD_VOLUME_ANOMALY,
       MODEL_NAME, MODEL_VERSION, MODEL_ALIAS
FROM ranked
WHERE RUN_RANK <= 12;

-- CI_ROLE: read-only (the GitHub late-run watchdog reads PIPELINE_RUNS). The database-level
-- future SELECT grants from setup_dev.sql already cover these tables; usage on the schema
-- and an explicit SELECT make it independent of creation order.
USE ROLE ACCOUNTADMIN;
GRANT USAGE  ON SCHEMA BANK_HEALTH.OPS                      TO ROLE CI_ROLE;
GRANT SELECT ON TABLE  BANK_HEALTH.OPS.PIPELINE_RUNS        TO ROLE CI_ROLE;
GRANT SELECT ON VIEW   BANK_HEALTH.OPS.HEALTH_RUNS_LAST_12  TO ROLE CI_ROLE;

-- -----------------------------------------------------------------------------
-- 2. DEV: BANK_HEALTH_DEV.OPS, owned by DEV_ROLE. Same tables and view, no PROD_SCHEDULER.
--    After a dev refresh (CREATE OR REPLACE DATABASE BANK_HEALTH_DEV CLONE BANK_HEALTH), the
--    clone brings prod's OPS rows with it: re-run this section, then
--      TRUNCATE TABLE BANK_HEALTH_DEV.OPS.PIPELINE_RUNS;   (and DBT_RESULTS, DBT_WARNINGS_AUDIT)
--    so dev volume checks compare dev runs with dev runs.
-- -----------------------------------------------------------------------------
USE ROLE DEV_ROLE;
CREATE SCHEMA IF NOT EXISTS BANK_HEALTH_DEV.OPS COMMENT = 'Dev pipeline operations (same shape as prod OPS)';
USE SCHEMA BANK_HEALTH_DEV.OPS;

CREATE TABLE IF NOT EXISTS PIPELINE_RUNS (
  RUN_ID               STRING        NOT NULL,  -- Airflow run id
  ENVIRONMENT          STRING        NOT NULL,  -- prod / dev
  HOST_ID              STRING,                  -- PROD_HOST_ID of the machine that ran it
  DAG_ID               STRING        NOT NULL,
  STARTED_AT           TIMESTAMP_TZ,
  ENDED_AT             TIMESTAMP_TZ,
  DURATION_SECONDS     NUMBER,
  STATE                STRING        NOT NULL,  -- success / failed
  FAILED_TASKS         STRING,                  -- comma-separated task ids, NULL if none
  BRONZE_ROWS          NUMBER,                  -- Databricks bronze, all tables
  SILVER_ROWS          NUMBER,                  -- Databricks silver, all tables
  GOLD_ROWS            NUMBER,                  -- Databricks gold.bank_quarter
  MARTS_ROWS           NUMBER,                  -- Snowflake MARTS, all tables
  SCORED_BANKS         NUMBER,                  -- rows in MARTS.MART_EARLY_WARNING
  ROWS_DETAIL          VARIANT,                 -- per-table counts behind the totals
  DBT_MODELS_OK        NUMBER,
  DBT_MODELS_ERROR     NUMBER,
  DBT_TESTS_PASS       NUMBER,
  DBT_TESTS_WARN       NUMBER,
  DBT_TESTS_FAIL       NUMBER,                  -- fail + error
  DQ_WARNINGS          NUMBER,                  -- data-quality warnings in the Databricks notebooks
  CREDITS_ESTIMATE     FLOAT,                   -- BANK_WH metering over the run window, at run end
  CREDITS_FINAL        FLOAT,                   -- the same, recomputed by the next run once complete
  MODEL_NAME           STRING,
  MODEL_VERSION        STRING,
  MODEL_ALIAS          STRING,
  LATEST_FDIC_QUARTER  DATE,
  VOLUME_ANOMALIES     VARIANT,                 -- what check_volumes flagged, if anything
  SLO_MET              BOOLEAN,                 -- succeeded by Monday 12:00 America/Chicago
  RECORDED_AT          TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS DBT_RESULTS (
  AIRFLOW_RUN_ID     STRING,
  INVOCATION_ID      STRING,
  NODE_ID            STRING,
  RESOURCE_TYPE      STRING,       -- model / test / ...
  NAME               STRING,
  STATUS             STRING,       -- success / error / pass / warn / fail / skipped
  FAILURES           NUMBER,       -- rows a test found
  EXECUTION_SECONDS  FLOAT,
  MESSAGE            STRING,
  RECORDED_AT        TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS DBT_WARNINGS_AUDIT (
  AIRFLOW_RUN_ID   STRING,
  TEST_NAME        STRING,
  NODE_ID          STRING,
  MODEL            STRING,
  COLUMN_NAME      STRING,
  FAILING_ROWS     NUMBER,
  KEY_COLUMNS      ARRAY,
  SAMPLE_KEYS      VARIANT,          -- up to 20 rows of key columns, e.g. [{"CERT":..,"REPORT_DATE":..}]
  RECORDED_AT      TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
);

-- Health page data (Step 8): the last 12 runs. Contract: docs/health_page_contract.md.
CREATE OR REPLACE VIEW HEALTH_RUNS_LAST_12 AS
WITH runs AS (
  SELECT r.*,
         COALESCE(r.CREDITS_FINAL, r.CREDITS_ESTIMATE) AS CREDITS,
         w.WARNED_ROWS
  FROM PIPELINE_RUNS r
  LEFT JOIN (SELECT AIRFLOW_RUN_ID, SUM(FAILING_ROWS) AS WARNED_ROWS
             FROM DBT_WARNINGS_AUDIT GROUP BY AIRFLOW_RUN_ID) w
    ON w.AIRFLOW_RUN_ID = r.RUN_ID
),
ranked AS (
  SELECT runs.*,
         AVG(CREDITS) OVER (ORDER BY STARTED_AT ROWS BETWEEN 3 PRECEDING AND CURRENT ROW) AS CREDITS_AVG_LAST_4,
         ROW_NUMBER() OVER (ORDER BY STARTED_AT DESC) AS RUN_RANK
  FROM runs
)
SELECT RUN_RANK, RUN_ID, ENVIRONMENT, HOST_ID, STARTED_AT, ENDED_AT,
       ROUND(DURATION_SECONDS / 60, 1)   AS DURATION_MINUTES,
       STATE, FAILED_TASKS,
       BRONZE_ROWS, SILVER_ROWS, GOLD_ROWS, MARTS_ROWS, SCORED_BANKS,
       DBT_MODELS_OK, DBT_MODELS_ERROR, DBT_TESTS_PASS, DBT_TESTS_WARN, DBT_TESTS_FAIL,
       COALESCE(WARNED_ROWS, 0)          AS WARNED_ROWS,
       DQ_WARNINGS,
       ROUND(CREDITS, 3)                 AS CREDITS,
       CREDITS_FINAL IS NOT NULL         AS CREDITS_ARE_FINAL,
       ROUND(CREDITS_AVG_LAST_4, 3)      AS CREDITS_AVG_LAST_4,
       LATEST_FDIC_QUARTER, SLO_MET, VOLUME_ANOMALIES IS NOT NULL AS HAD_VOLUME_ANOMALY,
       MODEL_NAME, MODEL_VERSION, MODEL_ALIAS
FROM ranked
WHERE RUN_RANK <= 12;

USE ROLE ACCOUNTADMIN;

-- -----------------------------------------------------------------------------
-- 3. Checks
-- -----------------------------------------------------------------------------
SHOW TABLES IN SCHEMA BANK_HEALTH.OPS;        -- 4 tables, owner BANK_PIPELINE_ROLE
SHOW TABLES IN SCHEMA BANK_HEALTH_DEV.OPS;    -- 3 tables, owner DEV_ROLE
SELECT * FROM BANK_HEALTH.OPS.PROD_SCHEDULER; -- one row: pc
SHOW GRANTS TO ROLE BANK_PIPELINE_ROLE;       -- includes MONITOR USAGE on the account
SHOW GRANTS TO ROLE CI_ROLE;                  -- USAGE on BANK_HEALTH.OPS, SELECT only
