# Phase 4, PR 10 (decision 4): the account is Pay As You Go, so quotas stop anything paid from
# being created by mistake. Quotas never touch running resources; they only refuse NEW launches,
# resizes or volumes beyond them.
#
# - Compute: every core and memory quota to zero, then the Always Free Arm shape (VM.Standard.A1)
#   back to exactly what the prod VM uses (4 OCPUs, 24 GB), per availability domain and per region.
#   Setting the regional A1 counts too matters: zeroing them while the VM uses 4 cores could block
#   a stop/start of the VM.
# - Block storage: 200 GB in total (the Always Free amount; the VM's boot volume uses 100 GB).
#
# Changing a number later = edit here, plan, Martins approves, apply.
resource "oci_limits_quota" "free_tier_only" {
  compartment_id = var.compartment_id # quotas live in the root compartment (the tenancy)
  name           = "bank-health-free-tier-only"
  description    = "Bank Health: only the Always Free Arm VM and 200 GB of block storage may be created (Pay As You Go guard)"
  statements = [
    "zero compute-core quotas in tenancy",
    "set compute-core quota standard-a1-core-count to 4 in tenancy",
    "set compute-core quota standard-a1-core-regional-count to 4 in tenancy",
    "zero compute-memory quotas in tenancy",
    "set compute-memory quota standard-a1-memory-count to 24 in tenancy",
    "set compute-memory quota standard-a1-memory-regional-count to 24 in tenancy",
    "set block-storage quota total-storage-gb to 200 in tenancy",
  ]

  lifecycle {
    prevent_destroy = true
  }
}
