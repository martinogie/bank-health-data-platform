# Imported from the live account on 3 Nov 2026 (terraform plan -generate-config-out), then
# tidied: only each object's own properties; parameters stay inherited from the account.
# prevent_destroy on everything (ADR 0010).

resource "snowflake_warehouse" "bank_dev_wh" {
  name             = "BANK_DEV_WH"
  comment          = "Bank Health dev and CI (dbt slim CI, dev pipeline runs)"
  warehouse_type   = "STANDARD"
  warehouse_size   = "XSMALL"
  generation       = "2"
  auto_suspend     = 60
  auto_resume      = "true"
  resource_monitor = "BANK_DEV_WH_MONITOR"

  lifecycle {
    prevent_destroy = true
    # Query acceleration is Enterprise-only (we are Standard); the provider's "not set" placeholders
    # would otherwise show as changes on import.
    ignore_changes = [enable_query_acceleration, query_acceleration_max_scale_factor]
  }
}

resource "snowflake_warehouse" "bank_ops_wh" {
  name           = "BANK_OPS_WH"
  comment        = "Bank Health ops checks from outside the VM (GitHub late-run watchdog). Under ACCOUNT_MONTHLY_CAP only."
  warehouse_type = "STANDARD"
  warehouse_size = "XSMALL"
  generation     = "2"
  auto_suspend   = 60
  auto_resume    = "true"

  lifecycle {
    prevent_destroy = true
    # Query acceleration is Enterprise-only (we are Standard); the provider's "not set" placeholders
    # would otherwise show as changes on import.
    ignore_changes = [enable_query_acceleration, query_acceleration_max_scale_factor]
  }
}

resource "snowflake_warehouse" "bank_wh" {
  name           = "BANK_WH"
  comment        = "Bank Health pipeline: loads, dbt and Power BI refresh"
  warehouse_type = "STANDARD"
  warehouse_size = "XSMALL"
  generation     = "2"
  auto_suspend   = 60
  auto_resume    = "true"

  lifecycle {
    prevent_destroy = true
    # Query acceleration is Enterprise-only (we are Standard); the provider's "not set" placeholders
    # would otherwise show as changes on import.
    ignore_changes = [enable_query_acceleration, query_acceleration_max_scale_factor]
  }
}

resource "snowflake_database" "bank_health" {
  name         = "BANK_HEALTH"
  comment      = "Bank Health Data Platform"
  is_transient = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_database" "bank_health_dev" {
  name         = "BANK_HEALTH_DEV"
  comment      = "Bank Health dev: zero-copy clone of BANK_HEALTH. Refresh: see the end of setup_dev.sql"
  is_transient = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_schema" "ops" {
  database            = "BANK_HEALTH"
  name                = "OPS"
  comment             = "Pipeline operations: run log, dbt results, warnings audit, scheduler guard, health view"
  is_transient        = "false"
  with_managed_access = "false"

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_schema" "raw" {
  database            = "BANK_HEALTH"
  name                = "RAW"
  is_transient        = "false"
  with_managed_access = "false"

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_schema" "staging" {
  database            = "BANK_HEALTH"
  name                = "STAGING"
  is_transient        = "false"
  with_managed_access = "false"

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_account_role" "bank_pipeline_role" {
  name    = "BANK_PIPELINE_ROLE"
  comment = "Airflow loads + dbt builds"

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_account_role" "bank_reporter_role" {
  name    = "BANK_REPORTER_ROLE"
  comment = "Read-only access to BANK_HEALTH.MARTS for Power BI"

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_account_role" "ci_role" {
  name    = "CI_ROLE"
  comment = "GitHub Actions dbt slim CI: CI_ schemas in BANK_HEALTH_DEV, read-only on BANK_HEALTH"

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_account_role" "dev_role" {
  name    = "DEV_ROLE"
  comment = "Dev pipeline + dbt dev: owns BANK_HEALTH_DEV only"

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_account_role" "ops_watchdog_role" {
  name    = "OPS_WATCHDOG_ROLE"
  comment = "GitHub late-run watchdog: read BANK_HEALTH.OPS.PIPELINE_RUNS on BANK_OPS_WH; read-only"

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_resource_monitor" "account_monthly_cap" {
  name                      = "ACCOUNT_MONTHLY_CAP"
  credit_quota              = 15
  frequency                 = "MONTHLY"
  start_timestamp           = "2026-10-25T23:11:11.583-05:00"
  notify_triggers           = [50, 80]
  notify_users              = []
  suspend_trigger           = 100
  suspend_immediate_trigger = 110

  lifecycle {
    prevent_destroy = true
  }
}

resource "snowflake_resource_monitor" "bank_dev_wh_monitor" {
  name            = "BANK_DEV_WH_MONITOR"
  credit_quota    = 10
  frequency       = "MONTHLY"
  start_timestamp = "2026-10-25T23:13:37.047-05:00"
  notify_triggers = [75]
  notify_users    = []
  suspend_trigger = 100

  lifecycle {
    prevent_destroy = true
  }
}

