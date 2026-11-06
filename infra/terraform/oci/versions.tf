# Oracle Cloud resources of the Bank Health platform, imported from what already runs prod (Phase 4).
# Import only: the first plan must show 0 to add, 0 to change, 0 to destroy, and NOTHING may touch
# the VM (scripts/tf_guard.py blocks any VM action). If the VM can't be imported without a change
# that would recreate or restart it, it stays out of Terraform (ADR 0010).

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
    key                 = "oci/terraform.tfstate"
    auth                = "APIKey"
    config_file_profile = "bank-health"
  }
}

# Auth: the API-key profile bank-health in ~/.oci/config (Martins' key file; never opened or printed).
provider "oci" {
  auth                = "APIKey"
  config_file_profile = "bank-health"
}

# Values in the git-ignored terraform.tfvars (identifiers, not secrets; kept out of the repo).
variable "compartment_id" {
  description = "OCID of the compartment holding the VM and its network (the tenancy root)"
  type        = string
}

variable "budget_alert_recipients" {
  description = "Email address(es) the $5 budget alert goes to"
  type        = string
}

variable "nat_public_ip_id" {
  description = "OCID of the NAT gateway's public IP (in terraform.tfvars)"
  type        = string
}

variable "vm_image_id" {
  description = "OCID of the image the VM was built from (in terraform.tfvars; the VM ignores all changes)"
  type        = string
}

variable "vm_private_ip_id" {
  description = "OCID of the VM's primary private IP, which the reserved public IP is assigned to (in terraform.tfvars)"
  type        = string
}
