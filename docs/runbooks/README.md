# Runbooks: index

Find the right page by what you see. Every alert names its task; start from the alert.

| You see | Go to |
|---|---|
| **FAILED: bank_health_weekly / \<task\>** in Slack or Discord | [alerts.md](alerts.md): what to do for each alert |
| … *Databricks Free Edition daily quota reached* | [quota_errors.md](quota_errors.md#databricks-free-edition-daily-quota) |
| … *Databricks refused the request (429 …)* | [quota_errors.md](quota_errors.md#databricks-refused-a-request-429) |
| … *Snowflake credit cap reached* (or the watchdog says so) | [quota_errors.md](quota_errors.md#snowflake-credit-cap-reached) |
| **FAILED … check_freshness** / **check_volumes** | [alerts.md](alerts.md) (first steps and thresholds) |
| **FAILED … check_contracts** (a source API changed) | [alerts.md](alerts.md); the contracts: `ingestion/contracts/`, [ADR 0007](../adr/0007-data-contracts-before-bronze.md) |
| **FAILED … publish_marts**, or wrong marts after a run | [marts_publish.md](marts_publish.md): swap back to last week's marts |
| **MODEL: monitoring found a shift or a weaker backtest** | [alerts.md](alerts.md); the numbers' meaning: [model card](../model_card.md#monitoring-finding-m-08-phase-3) |
| **LATE** / **WATCHDOG** / no OK message on Monday | [alerts.md](alerts.md) |
| Containers restarting with **Vault mode: missing …**, or **DEPLOY REFUSED: could not read the secrets from OCI Vault** | [vault.md](vault.md#troubleshooting) |
| **Snowflake refuses the pipeline's login** (IP not allowed) | [network_policy.md](network_policy.md#if-a-login-is-refused) |
| The VM is down or unreachable | [oracle_vm_setup.md](oracle_vm_setup.md); rollback to the PC until about 19 Nov 2026: [cutover_and_rollback.md](cutover_and_rollback.md) |
| A deploy failed, or you want to release a change | [deploy.md](deploy.md) |
| Data for a past period must be redone | [backfill.md](backfill.md) |
| A model to review, promote or roll back | [../runbook.md](../runbook.md) (top 20 review, promote, roll back to v4) |
| A credential is about to expire, or has leaked | [../../SECURITY.md](../../SECURITY.md); [rotation.md](rotation.md); prod secrets in Vault: [vault.md](vault.md#changing-a-secret-later-rotation) |

## Every runbook

| Runbook | About |
|---|---|
| [alerts.md](alerts.md) | Every alert, its thresholds, first steps; setting up the Slack and Discord webhooks |
| [quota_errors.md](quota_errors.md) | Databricks daily quota and Snowflake credit caps: what stops, how to resume |
| [marts_publish.md](marts_publish.md) | Write, audit, publish: how the marts are swapped in, and how to swap back |
| [backfill.md](backfill.md) | Redo a past period from the trigger form or the command line |
| [deploy.md](deploy.md) | How a merge reaches the VM: approval, the self-hosted runner, the smoke check |
| [oracle_vm_setup.md](oracle_vm_setup.md) | Building the Oracle VM from scratch |
| [cutover_and_rollback.md](cutover_and_rollback.md) | Moving prod between the PC and the VM, and back |
| [vault.md](vault.md) | Prod secrets in OCI Vault: setup, the switch, rollback, cleanup, changing a secret |
| [databricks_bundle.md](databricks_bundle.md) | The Databricks jobs as code (Asset Bundle): change, deploy, the prod workflow, its token |
| [network_policy.md](network_policy.md) | The VM's reserved IP and the Snowflake network policy on the pipeline user |
| [ssh_key_passphrase.md](ssh_key_passphrase.md) | A passphrase on the VM's SSH key, with the Windows ssh-agent |
| [../runbook.md](../runbook.md) | Model operations: top-20 review, promotion, rollback, the Databricks environment |

Service levels: [../slos.md](../slos.md). Decisions behind all of this: [../adr/](../adr/).
