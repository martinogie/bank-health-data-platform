# Security

How access, secrets and the network are set up, why, and what to do if something leaks. A one-person
portfolio project on public data: there is no personal or confidential data in it. What needs
protecting is the ability to run, change or spend on the infrastructure.

**Reporting a problem:** open a GitHub issue *without* any secret in it, or contact the owner
(github.com/martinogie).

## Who can do what

### Snowflake (account `CEWPSBS-TG88374`, paid Standard edition)

| User | Kind | Login | Role(s) | Can |
|---|---|---|---|---|
| `SVC_BANK_PIPELINE` | service | key pair only, no password | `BANK_PIPELINE_ROLE` | load `RAW`, build `STAGING` and `MARTS` in `BANK_HEALTH`, write `OPS`; uses `BANK_WH` |
| `SVC_BANK_DEV` | service | key pair only | `DEV_ROLE` | everything in `BANK_HEALTH_DEV` only; uses `BANK_DEV_WH`; nothing in prod |
| `SVC_BANK_CI` | service | key pair only | `CI_ROLE`, `OPS_WATCHDOG_ROLE` | read `BANK_HEALTH`; create temporary `CI_*` schemas in `BANK_HEALTH_DEV`; read `OPS.PIPELINE_RUNS` on `BANK_OPS_WH` for the late-run watchdog |
| `SVC_POWERBI` | service | key pair only | `BANK_REPORTER_ROLE` | read `BANK_HEALTH.MARTS` only |
| The owner | person | password + MFA | ACCOUNTADMIN and the above | everything; runs the setup SQL and Terraform |

- `OPS.PROD_SCHEDULER` (which machine may run prod) is owned by ACCOUNTADMIN; the pipeline can only
  read it, so it can't rewrite the rule that restrains it.
- Spending: resource monitors and an account budget (spending limit 5) stop runaway cost
  ([ADR 0004](docs/adr/0004-snowflake-with-a-postgresql-mirror.md)).
- **Network policy** (Phase 4, PR 7; planned): `SVC_BANK_PIPELINE` may log in only from the VM's
  reserved IP (plus the owner's home IP until 20 Nov 2026, while the PC is the rollback). The other
  service users get no policy, on purpose:
  - `SVC_BANK_CI` runs on GitHub-hosted runners, whose addresses are many and change; it is
    read-only on prod and writes only temporary schemas in dev.
  - `SVC_BANK_DEV` runs from the owner's PC on a changing home IP; it can't reach prod.
  - `SVC_POWERBI` runs from the owner's PC; it can only read the marts.

### Databricks (Free Edition workspace)

One workspace, one personal access token used by the pipeline (Airflow) to run the job and read its
results; catalogs `bank_health` (prod) and `bank_health_dev` (dev). Dev reads prod's `@champion`
model and, for comparisons only, prod tables, read-only; it never writes prod.

### GitHub

- `main` is protected: pull requests only, CI must pass. Deploys need the owner's approval in the
  `production` environment; a self-hosted runner on the VM applies them.
- Repository secrets: `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_CI_PRIVATE_KEY` (CI user),
  `SLACK_WEBHOOK_URL_PROD`, `DISCORD_WEBHOOK_URL_PROD` (the watchdog's alerts). Pull requests from
  forks don't receive secrets.

### Oracle Cloud (the production VM)

One Always Free Arm VM, Pay As You Go account with a $5 budget alert.

## Network exposure

- The VM accepts **SSH (port 22) from one IP only**, by key; no password or root login. Nothing else
  is open: the Airflow UI listens on `127.0.0.1` and is reached through an SSH tunnel.
- Everything else is outbound: the VM calls the FDIC and CFPB APIs, Databricks, Snowflake, GitHub,
  Slack and Discord.
- The PostgreSQL mirror and pgAdmin on the PC listen on `127.0.0.1` only.

## Where secrets live

| Secret | Today | Phase 4 (PR 8, planned) |
|---|---|---|
| Snowflake private key (pipeline) | file on the VM, `/srv/bank-health/secrets/keys`, mode 600, owner `deploy` | OCI Vault, encrypted with a passphrase (also in Vault), fetched into RAM at container start |
| Databricks token, Airflow Fernet and JWT keys, admin and Postgres passwords, Slack and Discord webhooks | `/srv/bank-health/secrets/.env`, mode 600 | OCI Vault, fetched into a RAM-only tmpfs (`/run/bank-health`) at container start |
| Dev keys and `.env` | the owner's PC, git-ignored | unchanged (dev only) |
| CI key, prod webhooks for the watchdog | GitHub encrypted secrets | unchanged |
| Terraform state (Phase 4) | – | private, versioned OCI Object Storage bucket; contains no secret values |

Nothing secret is committed: `.env`, `keys/`, `*.p8` and Terraform state are git-ignored, and CI
scans every push for secrets. Alert messages mask webhook URLs.

**Known exposure:** inside the containers, secrets are environment variables, so anyone with root or
Docker access on the VM can read them (`docker inspect`). Access to the VM is the owner's SSH key.

## If something leaks

| What leaked | Do this first | Then |
|---|---|---|
| A Snowflake private key | `ALTER USER <user> UNSET RSA_PUBLIC_KEY` (the old key stops working at once); set a new key | check `LOGIN_HISTORY` and `QUERY_HISTORY` for that user |
| The Databricks token | revoke it in Databricks (Settings → Developer → Access tokens); create a new one | check the job runs list for runs you didn't start |
| A Slack or Discord webhook | delete it in Slack or Discord; create a new one | update `.env` / Vault and the GitHub secret |
| The SSH key | remove its public key from `~/.ssh/authorized_keys` on the VM (through the OCI console's cloud shell if needed) | add a new key |
| The Airflow admin password | change it (`airflow users reset-password`) | the UI isn't on the internet, so exposure needs the SSH key too |
| A GitHub secret | replace it in GitHub, and rotate the credential it holds | check the Actions logs |

Rotation runbooks for every secret come with Phase 4, PR 8.
