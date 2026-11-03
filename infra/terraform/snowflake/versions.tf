# Snowflake objects of the Bank Health platform, imported from what already runs prod (Phase 4).
# Import only: the first plan must show 0 to add, 0 to change, 0 to destroy (scripts/tf_guard.py).
# Run by Martins' own user (encrypted key pair, ACCOUNTADMIN) through plan.ps1; no stored passphrase.

terraform {
  required_version = "= 1.16.6"

  required_providers {
    snowflake = {
      source  = "snowflakedb/snowflake"
      version = "= 2.22.1"
    }
  }

  # Remote state: the private, versioned OCI bucket. namespace and region come from
  # backend.hcl (git-ignored, copy backend.hcl.example; `terraform init -backend-config=backend.hcl`). Auth: the API-key
  # profile bank-health in ~/.oci/config (Martins' key file; Terraform reads it, nobody prints it).
  backend "oci" {
    bucket              = "bank-health-tfstate"
    key                 = "snowflake/terraform.tfstate"
    auth                = "APIKey"
    config_file_profile = "bank-health"
  }
}

variable "snowflake_user" {
  description = "Martins' own Snowflake LOGIN_NAME (not a secret; in the git-ignored terraform.tfvars)"
  type        = string
}

variable "snowflake_private_key_path" {
  description = "Path to Martins' passphrase-encrypted Terraform key (outside the repo; never opened by anyone else)"
  type        = string
}

# Login: key pair on Martins' own user (SNOWFLAKE_JWT), decided 3 Nov 2026. EXTERNALBROWSER needs an
# SSO provider (error 390190), password+MFA supports only Duo/TOTP (his MFA is a passkey), and
# Snowflake OAuth blocks ACCOUNTADMIN. The key file is encrypted; plan.ps1 asks for the passphrase
# in a hidden prompt and passes it in SNOWFLAKE_PRIVATE_KEY_PASSPHRASE for that one run only.
provider "snowflake" {
  organization_name = "CEWPSBS"
  account_name      = "TG88374"
  user              = var.snowflake_user
  authenticator     = "SNOWFLAKE_JWT"
  private_key       = file(var.snowflake_private_key_path)
  role              = "ACCOUNTADMIN"
}
