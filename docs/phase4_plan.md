# Phase 4 plan: infrastructure as code, security and operations

Agreed on 3 Nov 2026, with Martins' corrections and decisions. Source: the critical review, Phase 4
(Terraform, Databricks Asset Bundles, S-01, O-05, C-02, X-01, X-02, S-02, W-02), plus X-04
(independent validation) and the backlog. Status is updated here as each PR merges.

**Schedule.** All of Phase 4 in about 24 hours (3–4 Nov 2026). After every prod-touching merge, it is
deployed and verified straight away, before the next prod-touching merge: with a manual prod run
where Databricks is involved, otherwise with a check that doesn't use Databricks. Prod-touching:
contracts enforce, PR 2, PR 3, PR 7, PR 8, PR 9, PR 10. The others (PRs 0, 1, 4, 5, 6, 11) are built
in parallel while prod verifications run.

**Rules (unchanged).**
- Terraform imports only: never an apply without Martins seeing the plan; never a destroy, except
  objects whose names contain `SANDBOX`; `prevent_destroy` on prod resources; a guard script blocks
  deletes, replacements and any change to the VM. State is never in git.
- Martins handles every secret value; Claude Code never sees, pastes or logs one.
- Commits in Martins' name only. Every change tested in dev with CI green before he merges ("merge
  #N"). At most 2 dev Databricks runs a day. No deploys on Monday mornings around the 06:00 run.
  Nothing irreversible, public or destructive without his explicit OK. A safety guard or limit
  that stops the work means stop and ask.

## Order and what each PR touches

| # | PR | Findings | Touches prod | Verification |
|---|---|---|---|---|
| 0 | This plan | – | no | – |
| 1 | ADRs 0004–0009, runbook index, SLOs, SECURITY.md, README links | X-01, X-02, S-02 | no | – |
| 4 | Terraform foundation (remote state) and Snowflake import | Terraform | no: zero-change import | plan shows 0/0/0 |
| 5 | Snowflake grants import (or kept in SQL, see below) | Terraform | no | plan shows 0/0/0 |
| 6 | OCI import: VCN, subnet, gateway, route table, security list, VM, budget | Terraform | no | plan shows 0/0/0 |
| – | Data contracts: warn → **enforce** (Compose) | D-01 | yes | contract check on the latest files, no Databricks |
| 2 | Databricks minutes per run in `PIPELINE_RUNS` and the health view | C-02 | yes (Martins' SQL) | minutes from the last prod job run, no new run |
| 3 | Expiry checks, 14 days ahead, via the existing alerts | O-05 | yes (a check that never fails a run) | the check run on the VM |
| 8 | Secrets into OCI Vault, fetched at container start; key passphrase; rotation runbooks | S-01 | yes | **manual prod run** on Vault secrets |
| 7 | Reserved public IP for the VM; Snowflake network policy on `SVC_BANK_PIPELINE` | – | yes | pipeline user logs in from the VM |
| 9 | Databricks Asset Bundles: bind the two existing jobs; prod deploy through the production approval | DAB | yes | one dev deploy first; **manual prod run** |
| 10 | Backlog: `PROD_SCHEDULER` → `BANK_HEALTH.CONTROL`; OCI quota policies; `MARTS` ownership review; SSH key passphrase steps | – | yes (scheduler table) | `check_environment` against the new table |
| 11 | dbt docs built (not published); validation pack; yearly model review checklist; final docs pass | W-02, X-04 | no | – |
| – | SANDBOX demo: create, then destroy, a sandbox environment from code | Terraform | no | both plans shown first |

## Decisions (3 Nov 2026)

1. **Snowflake budget: it already exists** (`SET_SPENDING_LIMIT(5)`, a 50% forecast notification to
   Martins). Not recreated. The Snowflake provider (2.22.1) has no budget resource, so the budget
   stays SQL-managed; ADR 0010 records it.
2. **OCI budget alert: $5** (corrected everywhere).
3. **Grants:** imported only if they plan clean without changes. If not, they stay in the existing
   SQL scripts, with the reason in ADR 0010.
4. **OCI account: Pay As You Go.** Compartment quota policies block paid resources (zero paid-shape
   cores, a block-storage limit). The exact policy text is shown to Martins before he applies it.
5. **Network policy on `SVC_BANK_PIPELINE` only.** None on `SVC_BANK_DEV`, `SVC_POWERBI` or
   `SVC_BANK_CI`; SECURITY.md explains why. While the PC is the rollback (until about 19 Nov), the
   allowed list is the VM's reserved IP **and** Martins' home IP; a dated entry in
   `ops/credentials.yml` (and the expiry check) reminds to remove the home IP on **20 Nov 2026**.
6. **dbt docs: public**, but published only with the website, at `bank.martinsogiekpolor.com/dbt`.
   PR 11 builds the static site and proves no account identifier, hostname or data is in it.
   Nothing is published now.
7. **Independent validator:** a person Martins chooses later. PR 11 builds the validation pack
   regardless (scope, reproduce script, 20 checks, report template) and
   `docs/model_review_checklist.md`.
8. **SANDBOX demo:** yes, at the end, after clean imports. The guard script allows a destroy only for
   objects whose names contain `SANDBOX`; both plans (create, destroy) are shown first.
9. **Tools:** installed with winget (official packages): Terraform 1.16.6, OCI CLI 3.95.0,
   Databricks CLI 1.22.1. Providers pinned: `snowflakedb/snowflake` 2.22.1, `oracle/oci` 9.11.0.
10. **Databricks token for the GitHub bundle deploy:** created by Martins with a 90-day expiry, a
    GitHub `production` environment secret, covered by the PR 3 expiry check.
11. **Contracts mode: enforce**, now, as part of this push.
12. **Backlog addition:** a passphrase on the SSH key `bank_health_vm`, with runbook steps; Martins
    does the key work himself.

## Design notes

- **Terraform state:** OCI Object Storage bucket `bank-health-tfstate` (private, versioned,
  Oracle-managed encryption), through Terraform's native `oci` backend, which locks the state.
  `*.tfstate*` and `.terraform/` are git-ignored.
- **Logins (changed during the work, 3 Nov 2026; see [ADR 0010](adr/0010-terraform-import-only.md)):**
  OCI uses Martins' API-key profile `bank-health` (his choice, instead of a session token).
  Snowflake uses a passphrase-encrypted key pair on his own user, run by him through
  `infra/terraform/snowflake/plan.ps1`: `EXTERNALBROWSER` needs SSO, and password + MFA doesn't
  work with a passkey.
- **What Terraform manages:** warehouses, databases, the stable schemas (`RAW`, `STAGING`, `OPS`),
  roles, resource monitors; on OCI the network, the VM, the budget. **Not:** the 4 service users
  (kept in SQL, see ADR 0010), `MARTS` and `MARTS_NEXT` (swapped every week by write-audit-publish),
  `CI_*` schemas, dbt's tables and views, the `PROD_SCHEDULER` row, the Snowflake budget.
- **The VM** carries `prevent_destroy` and `ignore_changes = all`, and the guard blocks any action
  on it. Its import planned 0/0/0 and was applied on 3 Nov 2026 with no restart (up since 27 Oct).
- **Secrets (PR 8):** one Vault (software key, free tier); the VM reads them as itself (instance
  principal, a dynamic group and a policy limited to this vault), into a RAM-only tmpfs
  (`/run/bank-health`, mode 600) at container start. The old `.env` stays until a manual prod run
  succeeds on Vault; a written rollback step; any shred only after Martins' OK.
- **Network policy (PR 7):** a reserved public IP first (price checked before), then the policy, by
  SQL (Martins runs it), imported into Terraform afterwards.

## Risks

1. Imports that don't plan clean (provider defaults, grants): the code is changed to match reality,
   never the reverse; grants fall back to SQL.
2. The VM: protected as above; left out of Terraform if needed.
3. Provider changes: exact versions pinned; upgrades only in their own PR.
4. Locking prod out of Snowflake (PR 7): reserved IP first, a quiet window, verification from the VM,
   the home IP kept as a second entry.
5. The secrets cutover (PR 8): the old `.env` kept until a successful run on Vault; a rollback step.
6. Duplicate Databricks jobs (PR 9): the existing jobs are bound, verified with one dev deploy first.
7. Databricks quota: dev and prod share the workspace's daily quota; two manual prod runs today
   (PRs 8 and 9), dev runs only for PR 9.

## Costs

Terraform, Asset Bundles and the guard: free. OCI Object Storage (state), Vault (software key), quotas
and budgets: within Always Free. A reserved public IP: priced before it is created. Snowflake:
Terraform plans are metadata queries (about 0 credits); verification runs as before.
