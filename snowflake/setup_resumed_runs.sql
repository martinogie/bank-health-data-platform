-- =============================================================================
-- Bank Health Data Platform — resumed runs in the health view (website review, 6 Nov 2026).
-- Run in a Snowsight worksheet as ACCOUNTADMIN, BEFORE deploying the PR that changes record_run
-- (the view only reads ROWS_DETAIL, so either order works; old rows show RESUMED = FALSE).
-- In both BANK_HEALTH (prod) and BANK_HEALTH_DEV (dev):
--   OPS.HEALTH_RUNS_LAST_12  the view from setup_cost_per_run.sql, plus
--                            RESUMED, RESUMED_AT, ATTEMPTS, EARLIER_ATTEMPTS
-- Why: clearing a failed run's tasks re-runs record_run, which used to replace the failed row,
-- so a resumed run looked like a 20-second success and its failure vanished (6 Nov, 2 Nov).
-- record_run now keeps the first start and lists earlier attempts in ROWS_DETAIL:attempts.
-- Safe to re-run: views OR REPLACE ... COPY GRANTS (grants kept). No table changes, nothing is
-- dropped, no row changes. This view definition supersedes the one in setup_cost_per_run.sql.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. PROD
-- -----------------------------------------------------------------------------
USE ROLE BANK_PIPELINE_ROLE;
USE WAREHOUSE BANK_WH;
USE SCHEMA BANK_HEALTH.OPS;

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
       DATABRICKS_MINUTES,
       -- resumed after a fix (tasks cleared): the earlier attempts are kept (run_log.resume_info)
       COALESCE(ROWS_DETAIL:resumed::BOOLEAN, FALSE)        AS RESUMED,
       ROWS_DETAIL:resumed_at::TIMESTAMP_TZ                 AS RESUMED_AT,
       COALESCE(ARRAY_SIZE(ROWS_DETAIL:attempts), 0) + 1   AS ATTEMPTS,
       ROWS_DETAIL:attempts                                 AS EARLIER_ATTEMPTS
FROM ranked
WHERE RUN_RANK <= 12;

-- -----------------------------------------------------------------------------
-- 2. DEV (same shape)
-- -----------------------------------------------------------------------------
USE ROLE DEV_ROLE;
USE WAREHOUSE BANK_DEV_WH;
USE SCHEMA BANK_HEALTH_DEV.OPS;

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
       DATABRICKS_MINUTES,
       -- resumed after a fix (tasks cleared): the earlier attempts are kept (run_log.resume_info)
       COALESCE(ROWS_DETAIL:resumed::BOOLEAN, FALSE)        AS RESUMED,
       ROWS_DETAIL:resumed_at::TIMESTAMP_TZ                 AS RESUMED_AT,
       COALESCE(ARRAY_SIZE(ROWS_DETAIL:attempts), 0) + 1   AS ATTEMPTS,
       ROWS_DETAIL:attempts                                 AS EARLIER_ATTEMPTS
FROM ranked
WHERE RUN_RANK <= 12;

-- -----------------------------------------------------------------------------
-- 3. CHECK (expected results in the comments)
-- -----------------------------------------------------------------------------
USE ROLE ACCOUNTADMIN;
SELECT RUN_ID, STARTED_AT, STATE, RESUMED, ATTEMPTS FROM BANK_HEALTH.OPS.HEALTH_RUNS_LAST_12
 ORDER BY STARTED_AT DESC LIMIT 3;                          -- RESUMED = FALSE, ATTEMPTS = 1 (old rows)
SHOW GRANTS ON VIEW BANK_HEALTH.OPS.HEALTH_RUNS_LAST_12;   -- CI_ROLE: SELECT (kept by COPY GRANTS)
