# ADR 0010: Terraform for the stable infrastructure, import only

- **Status:** accepted (Phase 4, PRs 4–6, 3 Nov 2026)
- **Findings:** X-01 (infrastructure only in console clicks and SQL scripts)

## Context

The Oracle VM, its network, the OCI budget and the Snowflake warehouses, databases, roles and
monitors were created by hand (console and `snowflake/*.sql`). Nothing showed whether the live setup
still matched what we think it is, and rebuilding it would mean replaying clicks.

## Decision

Terraform describes what **already exists**, by importing it. It does not create the platform.

- **Versions pinned:** Terraform 1.16.6; providers `oracle/oci` 9.11.0 and `snowflakedb/snowflake`
  2.22.1 (lock files committed).
- **State:** a private, versioned OCI bucket `bank-health-tfstate` (Oracle-managed encryption)
  through Terraform's native `oci` backend, which also locks the state. State is never in git.
  The bucket itself is not managed by Terraform (it holds the state; bootstrapped by hand).
- **Logins (no stored secrets):**
  - OCI: Martins' API-key profile `bank-health` in `~/.oci/config` (the CLI, the provider and the
    state backend). The key file is his; nobody else opens it.
  - Snowflake: a passphrase-encrypted key pair on Martins' own user, run by him through
    `infra/terraform/snowflake/plan.ps1` (hidden passphrase prompt). Tried first and ruled out:
    `EXTERNALBROWSER` needs an SSO provider (error 390190); password + MFA supports only Duo or a
    6-digit code, and his MFA is a passkey; Snowflake OAuth blocks `ACCOUNTADMIN`.
- **Config from the live objects:** `terraform plan -generate-config-out`, then tidied by hand:
  - Snowflake: only each object's own properties. Account-inherited parameters (timezone, log
    levels, timeouts…) are left out; writing them would pin them on every object.
  - Snowflake: the provider's "not set" placeholders for Enterprise-only query acceleration are
    ignored (`ignore_changes`); we are on Standard.
  - OCI: the tenancy OCID, the budget's recipient address, the NAT IP and VM image OCIDs live in
    the git-ignored `terraform.tfvars`; Oracle's default tags (they hold an email) are dropped and
    ignored. The repo is public, so resource OCIDs and the Object Storage namespace stay out of
    git: the OCI import blocks were removed once the import was applied, and `backend.hcl` is
    git-ignored (`backend.hcl.example` is committed).
- **Protection:**
  - `prevent_destroy` on every imported resource.
  - The VM has `ignore_changes = all`: Terraform can never update, restart or replace it.
  - `scripts/tf_guard.py` runs on every saved plan. Import-only mode allows imports and no-ops,
    nothing else. Any action on the VM is always blocked. Deletes and replacements are allowed only
    for objects named `SANDBOX`, and only with `--allow-changes` (the SANDBOX demo).
  - Nothing is applied without Martins seeing the plan and approving it. The Snowflake apply is
    `plan.ps1 -Apply`: it applies only the saved plan, after re-running the guard.

## What is in Terraform

| Side | Resources |
|---|---|
| OCI (14, applied 3 Nov 2026, no drift afterwards) | VCN, public and private subnets, internet / NAT / service gateways, 2 route tables, 2 security lists, DHCP options, the VM `bank-health-prod`, the `bank-health-5usd` budget and its alert rule |
| Snowflake (15) | warehouses `BANK_WH`, `BANK_DEV_WH`, `BANK_OPS_WH`; databases `BANK_HEALTH`, `BANK_HEALTH_DEV`; schemas `RAW`, `STAGING`, `OPS`; roles `BANK_PIPELINE_ROLE`, `BANK_REPORTER_ROLE`, `DEV_ROLE`, `CI_ROLE`, `OPS_WATCHDOG_ROLE`; resource monitors `ACCOUNT_MONTHLY_CAP`, `BANK_DEV_WH_MONITOR` |

## What stays out, and why

| Object | Why |
|---|---|
| `MARTS`, `MARTS_NEXT` | swapped every week by write-audit-publish (ADR 0005); Terraform would fight the swap |
| `CI_*` schemas, dbt's tables and views, the `PROD_SCHEDULER` row | created and dropped by the pipeline and CI |
| The Snowflake account budget (`SET_SPENDING_LIMIT(5)`, 50% forecast notice) | the provider has no budget resource; it stays SQL-managed |
| The 4 service users (`SVC_BANK_PIPELINE`, `SVC_BANK_DEV`, `SVC_BANK_CI`, `SVC_POWERBI`) | with provider 2.22.1 their import plans an update of read-only outputs only (`show_output`, `parameters`; no settable field changes). The import-only guard rightly blocks any update, and Martins chose (3 Nov 2026) to keep the users in the SQL scripts rather than loosen the guard |
| Users' RSA public keys | rotation is a runbook step ([rotation.md](../runbooks/rotation.md)) |
| Martins' own Snowflake user | a person, not platform infrastructure |
| Grants (about 100, including 10 future grants) | kept in the SQL scripts (`snowflake/setup*.sql`), decided by Martins on 3 Nov 2026. The grants on `MARTS` travel with the schema that write-audit-publish swaps every week (ADR 0005); the future grants are set by the setup scripts; the objects dbt creates are dbt's. Managing part of this in Terraform and part in SQL or dbt would have two tools fighting over the same grants, and each of the remaining ~60 grants needs a hand-built import ID |
| The state bucket; Oracle's default "Tenant Admin Policy" | bootstrap and provider defaults |
| Schema `BANK_HEALTH.CONTROL` and its `PROD_SCHEDULER` row (PR 10) | created by `snowflake/setup_control.sql` as ACCOUNTADMIN; the schema can be imported later, the row stays SQL (cutover changes it) |

Created by Terraform (not imported): the OCI quota policy `bank-health-free-tier-only` (PR 10,
`infra/terraform/oci/quotas.tf`), applied only after Martins approved its plan.

## Consequences

- Drift shows up as a non-empty plan; a clean plan is evidence the live setup matches the code.
- Changes made on purpose (for example lowering the credit caps around 15 Nov 2026) go
  through a plan Martins approves, then the apply.
- The SQL scripts stay the record for what Terraform doesn't manage; the two must not both manage
  the same object.
