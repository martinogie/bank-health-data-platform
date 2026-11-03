-- =============================================================================
-- Bank Health Data Platform — model monitoring and contract results in OPS (Phase 3, PR 8; M-08).
-- Run in a Snowsight worksheet as ACCOUNTADMIN, AFTER setup_ops.sql. Run it BEFORE deploying PR 8
-- (until then the pipeline skips what isn't there yet and logs a warning; nothing breaks).
-- In both BANK_HEALTH (prod) and BANK_HEALTH_DEV (dev):
--   OPS.PIPELINE_RUNS           + CONTRACT_WARNINGS, CONTRACT_WOULD_FAIL (counts from check_contracts)
--   OPS.MODEL_MONITORING        one row per metric per run: PSI (score and each feature, vs the
--                               previous 4 quarters and vs 2001-2019 training) and the backtest
--                               (PR-AUC, top-10% capture per quarter from 2020); written by check_model
--   OPS.HEALTH_MODEL_MONITORING view for the health page: the latest run's monitoring headline
--   OPS.HEALTH_RUNS_LAST_12     the same view as before, plus the two contract columns
-- Owners as before: prod objects BANK_PIPELINE_ROLE, dev objects DEV_ROLE; CI_ROLE reads prod.
-- Safe to re-run: ADD COLUMN IF NOT EXISTS, CREATE TABLE IF NOT EXISTS, views OR REPLACE ... COPY GRANTS.
-- Nothing is dropped and no existing row changes.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. PROD
-- -----------------------------------------------------------------------------
USE ROLE BANK_PIPELINE_ROLE;
USE WAREHOUSE BANK_WH;
USE SCHEMA BANK_HEALTH.OPS;

ALTER TABLE PIPELINE_RUNS ADD COLUMN IF NOT EXISTS CONTRACT_WARNINGS NUMBER;    -- data-contract warnings this run
ALTER TABLE PIPELINE_RUNS ADD COLUMN IF NOT EXISTS CONTRACT_WOULD_FAIL NUMBER;  -- findings that would fail in enforce mode

CREATE TABLE IF NOT EXISTS MODEL_MONITORING (
  RUN_ID          STRING        NOT NULL,      -- the Airflow run that produced it
  ENVIRONMENT     STRING        NOT NULL,
  LATEST_QUARTER  DATE,                        -- the quarter being scored
  MODEL           STRING,                      -- e.g. bank_health.ml.distress_model v5
  KIND            STRING        NOT NULL,      -- psi | backtest
  REFERENCE       STRING,                      -- psi: previous_4q | training_2001_2019
  NAME            STRING        NOT NULL,      -- psi: score or a feature; backtest: pr_auc | top10_capture
  REPORT_DATE     DATE,                        -- backtest: the quarter judged
  VALUE           FLOAT,
  LEVEL           STRING,                      -- psi: stable | moderate | large
  ROWS_JUDGED     NUMBER,                      -- backtest: bank-quarters with a known outcome
  POSITIVES       NUMBER,                      -- backtest: of which entered distress
  JUDGED          BOOLEAN,                     -- backtest: at least 30 positives
  RECORDED_AT     TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE VIEW HEALTH_MODEL_MONITORING COPY GRANTS AS
WITH latest AS (
  SELECT * FROM MODEL_MONITORING
  QUALIFY RUN_ID = FIRST_VALUE(RUN_ID) OVER (ORDER BY RECORDED_AT DESC)
)
SELECT
  MAX(RUN_ID)                                                                                 AS RUN_ID,
  MAX(LATEST_QUARTER)                                                                         AS LATEST_QUARTER,
  MAX(MODEL)                                                                                  AS MODEL,
  MAX(IFF(KIND = 'psi' AND REFERENCE = 'previous_4q' AND NAME = 'score', VALUE, NULL))        AS SCORE_PSI_VS_PREVIOUS_4Q,
  MAX(IFF(KIND = 'psi' AND REFERENCE = 'training_2001_2019' AND NAME = 'score', VALUE, NULL)) AS SCORE_PSI_VS_TRAINING,
  COUNT_IF(KIND = 'psi' AND REFERENCE = 'previous_4q' AND NAME <> 'score' AND LEVEL = 'large') AS FEATURES_LARGE_SHIFT_VS_PREVIOUS_4Q,
  MAX(IFF(KIND = 'backtest' AND JUDGED, REPORT_DATE, NULL))                                   AS LATEST_JUDGED_QUARTER,
  MAX_BY(IFF(KIND = 'backtest' AND NAME = 'top10_capture' AND JUDGED, VALUE, NULL),
         IFF(KIND = 'backtest' AND NAME = 'top10_capture' AND JUDGED, REPORT_DATE, NULL))      AS LATEST_TOP10_CAPTURE
FROM latest;

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
       CONTRACT_WARNINGS, CONTRACT_WOULD_FAIL
FROM ranked
WHERE RUN_RANK <= 12;

USE ROLE ACCOUNTADMIN;
GRANT SELECT ON TABLE BANK_HEALTH.OPS.MODEL_MONITORING        TO ROLE CI_ROLE;
GRANT SELECT ON VIEW  BANK_HEALTH.OPS.HEALTH_MODEL_MONITORING TO ROLE CI_ROLE;

-- -----------------------------------------------------------------------------
-- 2. DEV (same shape)
-- -----------------------------------------------------------------------------
USE ROLE DEV_ROLE;
USE WAREHOUSE BANK_DEV_WH;
USE SCHEMA BANK_HEALTH_DEV.OPS;

ALTER TABLE PIPELINE_RUNS ADD COLUMN IF NOT EXISTS CONTRACT_WARNINGS NUMBER;
ALTER TABLE PIPELINE_RUNS ADD COLUMN IF NOT EXISTS CONTRACT_WOULD_FAIL NUMBER;

CREATE TABLE IF NOT EXISTS MODEL_MONITORING (
  RUN_ID STRING NOT NULL, ENVIRONMENT STRING NOT NULL, LATEST_QUARTER DATE, MODEL STRING,
  KIND STRING NOT NULL, REFERENCE STRING, NAME STRING NOT NULL, REPORT_DATE DATE, VALUE FLOAT,
  LEVEL STRING, ROWS_JUDGED NUMBER, POSITIVES NUMBER, JUDGED BOOLEAN,
  RECORDED_AT TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE VIEW HEALTH_MODEL_MONITORING COPY GRANTS AS
WITH latest AS (
  SELECT * FROM MODEL_MONITORING
  QUALIFY RUN_ID = FIRST_VALUE(RUN_ID) OVER (ORDER BY RECORDED_AT DESC)
)
SELECT
  MAX(RUN_ID) AS RUN_ID, MAX(LATEST_QUARTER) AS LATEST_QUARTER, MAX(MODEL) AS MODEL,
  MAX(IFF(KIND = 'psi' AND REFERENCE = 'previous_4q' AND NAME = 'score', VALUE, NULL))        AS SCORE_PSI_VS_PREVIOUS_4Q,
  MAX(IFF(KIND = 'psi' AND REFERENCE = 'training_2001_2019' AND NAME = 'score', VALUE, NULL)) AS SCORE_PSI_VS_TRAINING,
  COUNT_IF(KIND = 'psi' AND REFERENCE = 'previous_4q' AND NAME <> 'score' AND LEVEL = 'large') AS FEATURES_LARGE_SHIFT_VS_PREVIOUS_4Q,
  MAX(IFF(KIND = 'backtest' AND JUDGED, REPORT_DATE, NULL))                                   AS LATEST_JUDGED_QUARTER,
  MAX_BY(IFF(KIND = 'backtest' AND NAME = 'top10_capture' AND JUDGED, VALUE, NULL),
         IFF(KIND = 'backtest' AND NAME = 'top10_capture' AND JUDGED, REPORT_DATE, NULL))      AS LATEST_TOP10_CAPTURE
FROM latest;

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
       CONTRACT_WARNINGS, CONTRACT_WOULD_FAIL
FROM ranked
WHERE RUN_RANK <= 12;

-- -----------------------------------------------------------------------------
-- 3. CHECK (expected results in the comments)
-- -----------------------------------------------------------------------------
USE ROLE ACCOUNTADMIN;
SELECT COLUMN_NAME FROM BANK_HEALTH.INFORMATION_SCHEMA.COLUMNS
 WHERE TABLE_SCHEMA = 'OPS' AND TABLE_NAME = 'PIPELINE_RUNS' AND COLUMN_NAME LIKE 'CONTRACT%';   -- 2 rows
SHOW TABLES LIKE 'MODEL_MONITORING' IN SCHEMA BANK_HEALTH.OPS;        -- owner BANK_PIPELINE_ROLE
SHOW TABLES LIKE 'MODEL_MONITORING' IN SCHEMA BANK_HEALTH_DEV.OPS;    -- owner DEV_ROLE
SHOW GRANTS ON VIEW BANK_HEALTH.OPS.HEALTH_RUNS_LAST_12;              -- CI_ROLE: SELECT (kept by COPY GRANTS)
SHOW GRANTS ON VIEW BANK_HEALTH.OPS.HEALTH_MODEL_MONITORING;          -- CI_ROLE: SELECT
