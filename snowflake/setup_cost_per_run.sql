-- =============================================================================
-- Bank Health Data Platform — Databricks minutes per run (Phase 4, PR 2; finding C-02), and the
-- dates the Snowflake key pairs were set (Phase 4, PR 3; finding O-05).
-- Run in a Snowsight worksheet as ACCOUNTADMIN, AFTER setup_monitoring.sql, BEFORE deploying the PR
-- (until then the pipeline leaves the column out and logs a warning; nothing breaks).
-- In both BANK_HEALTH (prod) and BANK_HEALTH_DEV (dev):
--   OPS.PIPELINE_RUNS        + DATABRICKS_MINUTES (the job run's task minutes: setup + execution + cleanup)
--   OPS.HEALTH_RUNS_LAST_12  the same view as setup_monitoring.sql, plus DATABRICKS_MINUTES
-- Safe to re-run: ADD COLUMN IF NOT EXISTS; the views OR REPLACE ... COPY GRANTS (grants kept).
-- Nothing is dropped and no existing row changes. This view definition supersedes the one in
-- setup_monitoring.sql.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. PROD
-- -----------------------------------------------------------------------------
USE ROLE BANK_PIPELINE_ROLE;
USE WAREHOUSE BANK_WH;
USE SCHEMA BANK_HEALTH.OPS;

ALTER TABLE PIPELINE_RUNS ADD COLUMN IF NOT EXISTS DATABRICKS_MINUTES FLOAT;  -- Databricks job minutes this run

CREATE OR REPLACE VIEW HEALTH_RUNS_LAST_12 COPY GRANTS AS
WITH runs AS (
  SELECT r.*,
         COALESCE(r.CREDITS_FINAL, r.CREDITS_ESTIMATE) AS CREDITS,
         w.WARNED_ROWS
  FROM PIPELINE_RUNS r
  LEFT JOIN (SELECT AIRFLOW_RUN_ID, SUM(FAILING_ROWS) AS WARNED_ROWS
             FROM (SELECT AIRFLOW_RUN_ID, FAILING_ROWS FROM DBT_WARNINGS_AUDIT
                   QUALIFY ROW_NUMBER() OVER (PARTITION BY AIRFLOW_RUN_ID, NODE_ID
                                              ORDER BY RECORDED_AT DESC) = 1)  -- latest try per test
             GROUP BY AIRFLOW_RUN_ID) w
    ON w.AIRFLOW_RUN_ID = r.RUN_ID
),
ranked AS (
  SELECT runs.*,
         AVG(CREDITS) OVER (ORDER BY STARTED_AT NULLS LAST ROWS BETWEEN 3 PRECEDING AND CURRENT ROW) AS CREDITS_AVG_LAST_4,
         ROW_NUMBER() OVER (ORDER BY STARTED_AT DESC NULLS LAST) AS RUN_RANK
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
       MODEL_NAME, MODEL_VERSION, MODEL_ALIAS,
       CONTRACT_WARNINGS, CONTRACT_WOULD_FAIL,
       DATABRICKS_MINUTES
FROM ranked
WHERE RUN_RANK <= 12;

-- -----------------------------------------------------------------------------
-- 2. DEV (same shape)
-- -----------------------------------------------------------------------------
USE ROLE DEV_ROLE;
USE WAREHOUSE BANK_DEV_WH;
USE SCHEMA BANK_HEALTH_DEV.OPS;

ALTER TABLE PIPELINE_RUNS ADD COLUMN IF NOT EXISTS DATABRICKS_MINUTES FLOAT;

CREATE OR REPLACE VIEW HEALTH_RUNS_LAST_12 COPY GRANTS AS
WITH runs AS (
  SELECT r.*,
         COALESCE(r.CREDITS_FINAL, r.CREDITS_ESTIMATE) AS CREDITS,
         w.WARNED_ROWS
  FROM PIPELINE_RUNS r
  LEFT JOIN (SELECT AIRFLOW_RUN_ID, SUM(FAILING_ROWS) AS WARNED_ROWS
             FROM (SELECT AIRFLOW_RUN_ID, FAILING_ROWS FROM DBT_WARNINGS_AUDIT
                   QUALIFY ROW_NUMBER() OVER (PARTITION BY AIRFLOW_RUN_ID, NODE_ID
                                              ORDER BY RECORDED_AT DESC) = 1)
             GROUP BY AIRFLOW_RUN_ID) w
    ON w.AIRFLOW_RUN_ID = r.RUN_ID
),
ranked AS (
  SELECT runs.*,
         AVG(CREDITS) OVER (ORDER BY STARTED_AT NULLS LAST ROWS BETWEEN 3 PRECEDING AND CURRENT ROW) AS CREDITS_AVG_LAST_4,
         ROW_NUMBER() OVER (ORDER BY STARTED_AT DESC NULLS LAST) AS RUN_RANK
  FROM runs
)
SELECT RUN_RANK, RUN_ID, ENVIRONMENT, HOST_ID, STARTED_AT, ENDED_AT,
       ROUND(DURATION_SECONDS / 60, 1) AS DURATION_MINUTES,
       STATE, FAILED_TASKS,
       BRONZE_ROWS, SILVER_ROWS, GOLD_ROWS, MARTS_ROWS, SCORED_BANKS,
       DBT_MODELS_OK, DBT_MODELS_ERROR, DBT_TESTS_PASS, DBT_TESTS_WARN, DBT_TESTS_FAIL,
       COALESCE(WARNED_ROWS, 0) AS WARNED_ROWS,
       DQ_WARNINGS,
       ROUND(CREDITS, 3) AS CREDITS,
       CREDITS_FINAL IS NOT NULL AS CREDITS_ARE_FINAL,
       ROUND(CREDITS_AVG_LAST_4, 3) AS CREDITS_AVG_LAST_4,
       LATEST_FDIC_QUARTER, SLO_MET, VOLUME_ANOMALIES IS NOT NULL AS HAD_VOLUME_ANOMALY,
       MODEL_NAME, MODEL_VERSION, MODEL_ALIAS,
       CONTRACT_WARNINGS, CONTRACT_WOULD_FAIL,
       DATABRICKS_MINUTES
FROM ranked
WHERE RUN_RANK <= 12;

-- -----------------------------------------------------------------------------
-- 3. CHECK (expected results in the comments)
-- -----------------------------------------------------------------------------
USE ROLE ACCOUNTADMIN;
SELECT COLUMN_NAME FROM BANK_HEALTH.INFORMATION_SCHEMA.COLUMNS
 WHERE TABLE_SCHEMA = 'OPS' AND TABLE_NAME = 'PIPELINE_RUNS' AND COLUMN_NAME = 'DATABRICKS_MINUTES';  -- 1 row
SHOW GRANTS ON VIEW BANK_HEALTH.OPS.HEALTH_RUNS_LAST_12;   -- CI_ROLE: SELECT (kept by COPY GRANTS)

-- -----------------------------------------------------------------------------
-- 4. KEY DATES for ops/credentials.yml (read-only): send Martins' answer, the date only,
--    from the property RSA_PUBLIC_KEY_LAST_SET_TIME of each user (no key material is shown).
-- -----------------------------------------------------------------------------
DESC USER SVC_BANK_PIPELINE;
DESC USER SVC_BANK_DEV;
DESC USER SVC_BANK_CI;
DESC USER SVC_POWERBI;
