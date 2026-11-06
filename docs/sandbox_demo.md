# SANDBOX demo: create, then destroy, from code (Phase 4, decision 8)

The full Terraform cycle on something that isn't production: `infra/terraform/sandbox/` (one
private, empty Object Storage bucket, Always Free), with its **own state file**
(`sandbox/terraform.tfstate`), so nothing in it can reach the prod states (`oci/`, `snowflake/`).

Both plans were shown to Martins before each was applied; each apply used exactly the saved plan.

## 6 Nov 2026

| Step | Plan | Guard (import-only) | Guard (`--allow-changes`) | Result |
|---|---|---|---|---|
| Create (Martins: "apply sandbox") | 0 import, **1 add**, 0 change, 0 destroy | BLOCKED (a create) | PASS | `bank-health-sandbox-demo` created, private, empty |
| Destroy (Martins: "destroy sandbox") | 0 add, 0 change, **1 destroy** | BLOCKED (a delete) | PASS (the name contains "sandbox") | bucket gone (`404 BucketNotFound`); sandbox state: 0 resources |

What it shows:
- `scripts/tf_guard.py` refuses every create and delete in its normal mode; with `--allow-changes`
  it allows creates and updates, and a delete or replace only for objects named `SANDBOX`. Any
  action on the VM is always refused (unit tests: `ingestion/tests/test_tf_guard.py`).
- The prod states were not touched: the demo used only `sandbox/terraform.tfstate`; the OCI plan
  showed no changes after it (6 Nov); the Snowflake state was not used at all.

Run it again:

```powershell
cd infra/terraform/sandbox
terraform init -backend-config=backend.hcl     # backend.hcl: copy backend.hcl.example
terraform plan -out=tfplan; terraform show -json tfplan > plan.json
python ../../../scripts/tf_guard.py plan.json --allow-changes
terraform apply tfplan                          # only after the plan was approved
terraform plan -destroy -out=destroy.tfplan     # then the same guard check and approval
```
