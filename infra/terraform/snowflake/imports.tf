# What is imported (Phase 4, PR 4). The resource blocks themselves are generated from the live
# account (`terraform plan -generate-config-out=generated.tf`) and then tidied into the *.tf files,
# so the code describes what exists rather than what we think exists.
#
# Not managed here, on purpose (ADR 0010):
# - MARTS and MARTS_NEXT: swapped every week by write-audit-publish (ADR 0005);
# - CI_* schemas (temporary), dbt's tables and views, the PROD_SCHEDULER row;
# - the account budget (no provider resource; SQL-managed, SET_SPENDING_LIMIT(5));
# - Martins' own user;
# - the 4 service users (SVC_BANK_PIPELINE, SVC_BANK_DEV, SVC_BANK_CI, SVC_POWERBI): with provider
#   2.22.1 their import plans an update of read-only outputs only (show_output, parameters; no
#   settable field changes), which the import-only guard rightly blocks. Martins chose (3 Nov 2026)
#   to keep them in the SQL scripts (ADR 0010); the PR 7 network policy attaches separately.

# Warehouses
import {
  to = snowflake_warehouse.bank_wh
  id = "\"BANK_WH\""
}
import {
  to = snowflake_warehouse.bank_dev_wh
  id = "\"BANK_DEV_WH\""
}
import {
  to = snowflake_warehouse.bank_ops_wh
  id = "\"BANK_OPS_WH\""
}

# Databases
import {
  to = snowflake_database.bank_health
  id = "\"BANK_HEALTH\""
}
import {
  to = snowflake_database.bank_health_dev
  id = "\"BANK_HEALTH_DEV\""
}

# Stable prod schemas
import {
  to = snowflake_schema.raw
  id = "\"BANK_HEALTH\".\"RAW\""
}
import {
  to = snowflake_schema.staging
  id = "\"BANK_HEALTH\".\"STAGING\""
}
import {
  to = snowflake_schema.ops
  id = "\"BANK_HEALTH\".\"OPS\""
}

# Roles
import {
  to = snowflake_account_role.bank_pipeline_role
  id = "\"BANK_PIPELINE_ROLE\""
}
import {
  to = snowflake_account_role.bank_reporter_role
  id = "\"BANK_REPORTER_ROLE\""
}
import {
  to = snowflake_account_role.dev_role
  id = "\"DEV_ROLE\""
}
import {
  to = snowflake_account_role.ci_role
  id = "\"CI_ROLE\""
}
import {
  to = snowflake_account_role.ops_watchdog_role
  id = "\"OPS_WATCHDOG_ROLE\""
}

# Resource monitors
import {
  to = snowflake_resource_monitor.account_monthly_cap
  id = "\"ACCOUNT_MONTHLY_CAP\""
}
import {
  to = snowflake_resource_monitor.bank_dev_wh_monitor
  id = "\"BANK_DEV_WH_MONITOR\""
}
