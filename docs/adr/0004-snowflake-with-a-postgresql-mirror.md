# ADR 0004: Keep Snowflake (paid Standard, capped), with a local PostgreSQL mirror for browsing

- **Status:** accepted (Phase 2, 29 Oct 2026)
- **Findings:** C-01 (the Snowflake trial ends around 18 November 2026)

## Context

The marts lived in a 30-day Snowflake trial. After it, loads and dbt would fail unless a payment
method was added. The review offered four options: pay with a cap, freeze the pipeline, move the
marts to PostgreSQL, or use Databricks SQL or DuckDB for the report.

What mattered: a pipeline that keeps running for anyone who clones the repo or opens the website;
Snowflake as a real, used tool rather than "used in development"; a cost that can't run away.

## Decision

1. **Keep Snowflake as the warehouse, on a paid Standard account** (since 29 Oct 2026), with three
   independent limits:
   - resource monitors that suspend warehouses: `ACCOUNT_MONTHLY_CAP` on the whole account and
     `BANK_DEV_WH_MONITOR` on dev ([quota_errors.md](../runbooks/quota_errors.md));
   - an account **budget** with a spending limit of 5 (`SET_SPENDING_LIMIT(5)`) and a notification
     at 50% of the forecast;
   - X-Small warehouses with 60-second auto-suspend.
2. **Standard edition only:** no Enterprise feature is used (no multi-cluster warehouses, no
   masking policies, no 90-day Time Travel).
3. **A local PostgreSQL 16 mirror** of every layer, with pgAdmin, for browsing and comparing tables
   with plain SQL (`postgres/`, `scripts/mirror_to_postgres.py`). It only reads Snowflake and
   Databricks; nothing in production depends on it.

## Alternatives considered

| Option | Why not |
|---|---|
| Freeze the pipeline with a final snapshot | A portfolio pipeline that no longer runs is weaker than one that does |
| Move the marts to PostgreSQL (dbt-postgres) | Free, but Snowflake becomes "used once"; the warehouse, roles and cost controls are part of what the project shows |
| Databricks SQL or DuckDB for the report | Free Edition's SQL warehouse shares the daily compute quota with the pipeline |

## Consequences

- A real but small monthly cost (a prod run is about 0.21–0.31 credits), with three limits that stop
  spending before it matters. A limit reached stops the pipeline with a clear alert
  ([quota_errors.md](../runbooks/quota_errors.md)); raising a limit is the owner's decision.
- The mirror is a convenience; if it goes stale, nothing breaks.
