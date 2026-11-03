# Service levels

What "working" means for the weekly pipeline, how each level is measured, and what happens when one
is missed. Measured from `BANK_HEALTH.OPS.PIPELINE_RUNS` (one row per run) and its view
`OPS.HEALTH_RUNS_LAST_12`.

## Objectives

| # | Objective | Target | Measured by | When missed |
|---|---|---|---|---|
| 1 | **Fresh data:** this week's marts and watchlist are ready by **Monday 12:00 America/Chicago** | every week | `SLO_MET` on the scheduled run's row | LATE alert from the VM at 12:00 and from GitHub at 12:30 CST ([alerts.md](runbooks/alerts.md)) |
| 2 | **Success rate:** scheduled runs that succeed without a manual fix | at least 11 of the last 12 | `STATE` in `HEALTH_RUNS_LAST_12` (manual overrides don't count as unattended) | the failure alert; the cause is fixed in a PR |
| 3 | **Correct, not just green:** freshness, volume, match-rate and data-contract checks pass | every run | `check_freshness`, `check_volumes`, `check_contracts` | the run fails and the watchlist is not published |
| 4 | **Cost per run:** Snowflake credits | about 0.21–0.31 a run; the account cap is 15 credits a month for now (raised for the Phase 4 dev work; lowered when it is done) | `CREDITS_FINAL` (or `CREDITS_ESTIMATE`) per run; `ACCOUNT_MONTHLY_CAP` | the monitor suspends warehouses; [quota_errors.md](runbooks/quota_errors.md) |
| 5 | **Databricks time per run** | within the Free Edition daily quota | Databricks minutes per run (Phase 4, PR 2; planned) | quota alert; resume the next day |

## Service indicators (watched, not targets)

| Indicator | Normal today | Alert |
|---|---|---|
| Complaint match rate | 31.68% with the E-01 fixes, measured on prod data in a dev rebuild (30.04% before); first prod run 9 Nov 2026 | more than 2 points below the usual |
| Model drift (score PSI vs the previous 4 quarters) | 0.008 (3 Nov 2026, dev on prod data) | above 0.25 |
| Backtest top-10% capture | 45.1%–59.9% per quarter, 2020–2025 | more than 10 points below 52.6% |
| Data-contract findings | 0 would-fail findings | any breaking change fails the run (enforce mode, from Phase 4) |

Reasons for each threshold: [ADR 0008](adr/0008-monitoring-thresholds.md).

## Error budget

One missed week in 12 (objective 2). A second miss in the same 12 weeks pauses feature work until
the cause is fixed and a run succeeds unattended.
