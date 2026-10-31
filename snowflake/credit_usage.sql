-- Where did the Snowflake credits go? READ-ONLY: it changes nothing.
-- Run as ACCOUNTADMIN in a Snowsight worksheet (ACCOUNT_USAGE needs it). Uses BANK_WH for its own
-- queries, so it still works when BANK_DEV_WH is suspended by its monitor (and costs a few
-- seconds of an XSMALL warehouse).
--
-- Notes:
-- - ACCOUNT_USAGE lags: metering up to ~3 hours, query history up to ~45 minutes.
-- - Snowflake bills a warehouse per second while it runs, with a 60-second minimum each time it
--   resumes, plus the idle time before AUTO_SUSPEND (60 s here). Per-query credits are therefore
--   an estimate: each day's metered credits are shared out by each source's share of execution
--   time that day. Many short, separate bursts cost more than their execution time suggests.

USE ROLE ACCOUNTADMIN;
USE WAREHOUSE BANK_WH;

-- 1. The caps: used and remaining credits, and when each resets (next interval after start_time).
SHOW RESOURCE MONITORS;

-- 2. Credits per day per warehouse since 26 Oct 2026 (compute + cloud services).
SELECT
    TO_DATE(start_time)                      AS day,
    warehouse_name,
    ROUND(SUM(credits_used_compute), 3)      AS compute_credits,
    ROUND(SUM(credits_used_cloud_services), 3) AS cloud_services_credits,
    ROUND(SUM(credits_used), 3)              AS credits,
    COUNT_IF(credits_used > 0)               AS hours_with_usage
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
WHERE start_time >= '2026-10-26'
GROUP BY 1, 2
ORDER BY 1, 2;

-- 3. BANK_DEV_WH by source and day: queries, execution time, and an estimate of the credits.
WITH q AS (
    SELECT
        TO_DATE(start_time) AS day,
        CASE
            WHEN user_name = 'SVC_BANK_CI' AND query_text ILIKE '%"app": "dbt"%'      THEN 'dbt slim CI'
            WHEN user_name = 'SVC_BANK_CI'                                            THEN 'CI other (late-run watchdog)'
            WHEN user_name = 'SVC_BANK_DEV' AND query_text ILIKE '%"app": "dbt"%'     THEN 'dev pipeline: dbt build'
            WHEN user_name = 'SVC_BANK_DEV'
                 AND (query_text ILIKE 'PUT %' OR query_text ILIKE '%COPY INTO%'
                      OR query_text ILIKE '%__LOADING%' OR query_text ILIKE '%TEMPORARY STAGE%') THEN 'dev pipeline: Snowflake load'
            WHEN user_name = 'SVC_BANK_DEV'                                           THEN 'dev pipeline: checks, run log, metrics'
            ELSE 'ad-hoc (' || user_name || ', ' || COALESCE(role_name, '?') || ')'
        END AS source,
        execution_time / 1000 AS exec_seconds
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
    WHERE warehouse_name = 'BANK_DEV_WH'
      AND start_time >= '2026-10-26'
),
by_source AS (
    SELECT day, source, COUNT(*) AS queries, ROUND(SUM(exec_seconds) / 60, 1) AS exec_minutes
    FROM q
    GROUP BY 1, 2
),
metered AS (
    SELECT TO_DATE(start_time) AS day, SUM(credits_used) AS credits
    FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
    WHERE warehouse_name = 'BANK_DEV_WH' AND start_time >= '2026-10-26'
    GROUP BY 1
)
SELECT
    s.day,
    s.source,
    s.queries,
    s.exec_minutes,
    ROUND(m.credits * s.exec_minutes / NULLIF(SUM(s.exec_minutes) OVER (PARTITION BY s.day), 0), 3)
        AS est_credits
FROM by_source s
LEFT JOIN metered m ON m.day = s.day
ORDER BY s.day, est_credits DESC NULLS LAST;

-- 4. The same, totalled over the whole period, biggest first.
WITH q AS (
    SELECT
        TO_DATE(start_time) AS day,
        CASE
            WHEN user_name = 'SVC_BANK_CI' AND query_text ILIKE '%"app": "dbt"%'      THEN 'dbt slim CI'
            WHEN user_name = 'SVC_BANK_CI'                                            THEN 'CI other (late-run watchdog)'
            WHEN user_name = 'SVC_BANK_DEV' AND query_text ILIKE '%"app": "dbt"%'     THEN 'dev pipeline: dbt build'
            WHEN user_name = 'SVC_BANK_DEV'
                 AND (query_text ILIKE 'PUT %' OR query_text ILIKE '%COPY INTO%'
                      OR query_text ILIKE '%__LOADING%' OR query_text ILIKE '%TEMPORARY STAGE%') THEN 'dev pipeline: Snowflake load'
            WHEN user_name = 'SVC_BANK_DEV'                                           THEN 'dev pipeline: checks, run log, metrics'
            ELSE 'ad-hoc (' || user_name || ', ' || COALESCE(role_name, '?') || ')'
        END AS source,
        execution_time / 1000 AS exec_seconds
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
    WHERE warehouse_name = 'BANK_DEV_WH'
      AND start_time >= '2026-10-26'
),
daily AS (
    SELECT day, source, SUM(exec_seconds) AS secs, SUM(SUM(exec_seconds)) OVER (PARTITION BY day) AS day_secs
    FROM q
    GROUP BY 1, 2
),
metered AS (
    SELECT TO_DATE(start_time) AS day, SUM(credits_used) AS credits
    FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
    WHERE warehouse_name = 'BANK_DEV_WH' AND start_time >= '2026-10-26'
    GROUP BY 1
)
SELECT
    d.source,
    ROUND(SUM(d.secs) / 60, 1)                                   AS exec_minutes,
    ROUND(SUM(m.credits * d.secs / NULLIF(d.day_secs, 0)), 3)     AS est_credits
FROM daily d
LEFT JOIN metered m ON m.day = d.day
GROUP BY 1
ORDER BY est_credits DESC NULLS LAST;

-- 5. How many separate times BANK_DEV_WH woke up per day (each costs at least 60 s + 60 s idle).
SELECT
    TO_DATE(timestamp) AS day,
    COUNT_IF(event_name = 'RESUME_WAREHOUSE') AS resumes,
    COUNT_IF(event_name = 'SUSPEND_WAREHOUSE') AS suspends
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_EVENTS_HISTORY
WHERE warehouse_name = 'BANK_DEV_WH' AND timestamp >= '2026-10-26'
GROUP BY 1
ORDER BY 1;
