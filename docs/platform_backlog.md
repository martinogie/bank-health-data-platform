# Platform backlog (Phase 4: infrastructure, security and operations)

Items agreed for Phase 4, collected from the critical review and from Phases 2–3. Model work is
in [ROADMAP.md](ROADMAP.md); Phase 3 is in [phase3_plan.md](phase3_plan.md).

| Item | Why | Source |
|---|---|---|
| Terraform for the Snowflake objects and the Oracle VM | A new environment comes up from code with one command | Review, Phase 4 |
| Databricks Asset Bundles for the job | The job definition lives in the repo, not only in the UI | Review, Phase 4 |
| OCI Vault for secrets, encrypted keys, a rotation runbook (S-01) | No plain-text secrets on disk; today the VM's `.env` and key are files readable only by `deploy` | Review S-01; [ADR 0001](adr/0001-oracle-vm-and-self-hosted-runner.md) |
| A passphrase on the Snowflake private key | The key is unencrypted today; it fits with the Vault work | Phase 2 decision |
| Token expiry checks (O-05) | The Databricks token and key pairs expire; a check warns 14 days ahead, with a rotation runbook | Review O-05 |
| Cost per run (C-02) | Credits per run are in `OPS.PIPELINE_RUNS`; Phase 4 adds the Databricks side and a monthly view | Review C-02 |
| `SECURITY.md` (S-02), runbook and SLO links (X-02) | One place to find the security notes and operating rules | Review S-02, X-02 |
| Published dbt docs (W-02) | The lineage site is generated each run but not hosted | Review W-02 |
| Move `PROD_SCHEDULER` into its own ACCOUNTADMIN-owned schema | Today the table sits in `BANK_HEALTH.OPS`, a schema the pipeline role owns. The table itself is owned by ACCOUNTADMIN and the pipeline can only read it, but a schema of its own makes the boundary plain and stops the schema owner from dropping or replacing it | Phase 2 review of `setup_ops.sql` |
