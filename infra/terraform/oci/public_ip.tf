# Phase 4, PR 7: the VM's reserved public IP (created in the console by Martins on 6 Nov 2026,
# then imported here). It replaced the ephemeral IP, so the address never changes: the Snowflake
# network policy on SVC_BANK_PIPELINE allows it (snowflake/setup_network_policy.sql). $0 (Oracle's
# price list has no public-IP charge). The address itself is in the OCI console and the runbook.
resource "oci_core_public_ip" "bank_health_prod" {
  compartment_id = var.compartment_id
  display_name   = "bank-health-prod-ip"
  freeform_tags  = {}
  lifetime       = "RESERVED"
  private_ip_id  = var.vm_private_ip_id

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [defined_tags]
  }
}
