# SANDBOX demo (Phase 4, decision 8): create, then destroy, a small environment from code, to show
# the full Terraform cycle on something that isn't production.
#
# - Its own state file (sandbox/terraform.tfstate), so nothing here can touch the prod states.
# - Every name contains "sandbox": scripts/tf_guard.py allows a destroy only for such objects, and
#   only in --allow-changes mode. Nothing on the VM, ever.
# - Cost: one empty private Object Storage bucket (Always Free; 0 bytes stored).
# - Both plans (create, destroy) are shown to Martins before either is applied.

terraform {
  required_version = "= 1.16.6"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 9.11.0"
    }
  }

  # namespace and region come from backend.hcl (git-ignored; copy backend.hcl.example).
  backend "oci" {
    bucket              = "bank-health-tfstate"
    key                 = "sandbox/terraform.tfstate"
    auth                = "APIKey"
    config_file_profile = "bank-health"
  }
}

provider "oci" {
  auth                = "APIKey"
  config_file_profile = "bank-health"
}

variable "compartment_id" {
  description = "OCID of the compartment (in the git-ignored terraform.tfvars)"
  type        = string
}

data "oci_objectstorage_namespace" "this" {
  compartment_id = var.compartment_id
}

resource "oci_objectstorage_bucket" "sandbox" {
  compartment_id = var.compartment_id
  namespace      = data.oci_objectstorage_namespace.this.namespace
  name           = "bank-health-sandbox-demo"
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  versioning     = "Disabled"
  freeform_tags = {
    purpose = "sandbox-demo"
    phase   = "4"
  }
}

output "sandbox_bucket" {
  value = oci_objectstorage_bucket.sandbox.name
}
