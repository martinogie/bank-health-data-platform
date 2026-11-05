# Runbook: prod secrets in OCI Vault

Phase 4, PR 8. The prod VM reads its secrets from OCI Vault **as itself** (instance principal: no key
or token stored for that), into RAM (`/run/bank-health`, a tmpfs that is gone at power-off). The
containers get them as environment variables when they start, exactly as from the `.env` file
before, so the pipeline code is unchanged. Nothing secret stays on disk once the cleanup is done.

## What is where

| Vault secret | Becomes | Used by |
|---|---|---|
| `prod-databricks-token` | `DATABRICKS_TOKEN` | Databricks job runs and file uploads |
| `prod-snowflake-private-key` | `/run/bank-health/keys/snowflake_rsa_key.p8` (encrypted) | the Snowflake loads and dbt, as `SVC_BANK_PIPELINE` |
| `prod-snowflake-key-passphrase` | `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` | opens that key |
| `prod-airflow-fernet-key` | `AIRFLOW__CORE__FERNET_KEY` | Airflow encrypts its stored connections and variables with it |
| `prod-airflow-jwt-secret` | `AIRFLOW__API_AUTH__JWT_SECRET` | Airflow's API and task tokens |
| `prod-airflow-admin-password` | `_AIRFLOW_WWW_USER_PASSWORD` | the admin user, created by `airflow-init` |
| `prod-slack-webhook`, `prod-discord-webhook` | `SLACK_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL` | alerts |

- Vault `bank-health-vault` (standard vault, software key `bank-health-secrets-key`: free).
  Its OCID is in `/srv/bank-health/app/airflow/.env` as `BANK_HEALTH_VAULT_ID` (not secret, but
  kept out of the public repo).
- Access: dynamic group `bank-health-vm-dg` (only the prod VM) and policy
  `bank-health-vm-read-secrets` (read secret bundles, this vault only). The VM can't list, change
  or delete anything.
- The code: `deploy/vault_secrets.py` (host: Vault → RAM), `deploy/bank-health-secrets.service`
  (at boot), `airflow/include/load_secrets.sh` (container: RAM → environment), `deploy/deploy.sh`
  (refreshes the RAM copy on every deploy).
- **Not in Vault, on purpose:** the Postgres mirror and pgAdmin passwords (`POSTGRES_PASSWORD`,
  `POSTGRES_VIEWER_PASSWORD`, `PGADMIN_PASSWORD`). The mirror runs on Martins' PC only, not on the
  VM; their copies on the VM are removed in the cleanup. Airflow's own metadata database password
  (`prod-airflow-db-password`) is changed later, in its own small PR with its own rollback.

## The two modes

| | `.env` mode | Vault mode |
|---|---|---|
| Set by | nothing (the default; dev always) | `BANK_HEALTH_RUNTIME_SECRETS_DIR=/run/bank-health` in `airflow/.env` |
| Secrets come from | `/srv/bank-health/secrets/.env` and `keys/` | `/run/bank-health/env/*` and `/run/bank-health/keys/` |
| If one is missing | the task that needs it fails | **the container doesn't start** ("Vault mode: missing ...") |

Merging PR 8 changes nothing on its own: prod stays in `.env` mode until the switch below.

## One-time setup (Martins, as admin: `ssh bank-health-vm-admin`)

```bash
# 1. Python's venv package (Ubuntu doesn't ship it by default)
sudo apt-get update && sudo apt-get install -y python3.12-venv

# 2. The small Python environment for the fetch script, owned by deploy
sudo -u deploy -H python3 -m venv /srv/bank-health/venv
sudo -u deploy -H /srv/bank-health/venv/bin/pip install --quiet -r /srv/bank-health/app/deploy/requirements-vault.txt

# 3. The vault OCID (Identity & Security -> Vault -> bank-health-vault -> copy OCID)
sudo -u deploy -H bash -c 'echo "BANK_HEALTH_VAULT_ID=<paste the vault OCID>" >> /srv/bank-health/app/airflow/.env'

# 4. The boot-time fetch: creates /run/bank-health and reads the Vault now
sudo cp /srv/bank-health/app/deploy/bank-health-secrets.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bank-health-secrets.service
systemctl status bank-health-secrets.service --no-pager | head -5   # "status=0/SUCCESS"

# 5. Check: the files exist (names only), and Vault equals today's .env where it should
sudo ls /run/bank-health/env /run/bank-health/keys
sudo -u deploy -H /srv/bank-health/venv/bin/python /srv/bank-health/app/deploy/vault_secrets.py \
    check-env /srv/bank-health/secrets/.env
```

Step 5 must show `matches` for the 6 copied values and `differs by design` for the key and its
passphrase. A `DIFFERS` means a copy-paste slip: fix that secret in the console (new version), then
`sudo systemctl restart bank-health-secrets.service` and check again. The stack is still in `.env`
mode during all of this; nothing it uses has changed.

## The switch (not on a Monday morning; no run in progress)

```bash
ssh bank-health-vm            # as deploy
cd /srv/bank-health/app/airflow
cp .env .env.before-vault     # the rollback copy (paths only, no secrets)
sed -i 's#^BANK_HEALTH_KEYS_DIR=.*#BANK_HEALTH_KEYS_DIR=/run/bank-health/keys#' .env
echo 'BANK_HEALTH_RUNTIME_SECRETS_DIR=/run/bank-health' >> .env
docker compose up -d --wait --wait-timeout 600
/srv/bank-health/app/deploy/smoke_check.sh
```

Then check, and start **one manual prod run** from the UI (the Databricks part is needed to prove
the token and the key):

```bash
docker compose ps                                                  # all healthy
docker compose exec -T airflow-scheduler bash -c 'head -1 /opt/airflow/keys/snowflake_rsa_key.p8'
#   -----BEGIN ENCRYPTED PRIVATE KEY-----   (the Vault copy, not the old file)
```

## Rollback (any time before the cleanup)

```bash
ssh bank-health-vm
cd /srv/bank-health/app/airflow
cp .env.before-vault .env
docker compose up -d --wait --wait-timeout 600
/srv/bank-health/app/deploy/smoke_check.sh
```

The `.env` file and `keys/` are untouched until the cleanup, so this is the exact previous setup.
If a code problem is found, also revert the PR 8 merge on GitHub (Revert button, merge, approve the
deploy).

## Cleanup (only after the manual prod run on Vault succeeded, and with Martins' OK)

What goes, on the VM: from `/srv/bank-health/secrets/.env` the lines `DATABRICKS_TOKEN`,
`SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`, `AIRFLOW__CORE__FERNET_KEY`, `AIRFLOW__API_AUTH__JWT_SECRET`,
`_AIRFLOW_WWW_USER_PASSWORD`, `SLACK_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL` and the mirror leftovers
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_VIEWER_PASSWORD`, `PGADMIN_EMAIL`, `PGADMIN_PASSWORD`;
the editor backup `/srv/bank-health/secrets/.env.save`; the unencrypted key
`/srv/bank-health/secrets/keys/snowflake_rsa_key.p8`; and `.env.before-vault`. The exact commands
come with the cleanup PR. After it, Vault is the only copy (plus Martins' password manager), and
the rollback above no longer applies.

After the cleanup and a `docker compose up -d`, Docker's stored container settings hold no secret
(this prints 0):

```bash
docker inspect airflow-airflow-scheduler-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1 \
  | grep -cxE 'DATABRICKS_TOKEN|SNOWFLAKE_PRIVATE_KEY_PASSPHRASE|AIRFLOW__CORE__FERNET_KEY|AIRFLOW__API_AUTH__JWT_SECRET|_AIRFLOW_WWW_USER_PASSWORD|SLACK_WEBHOOK_URL|DISCORD_WEBHOOK_URL'
```

## Changing a secret later (rotation)

1. Console: open the secret → **Create Secret Version** → paste the new value.
2. On the VM, as deploy: `/srv/bank-health/venv/bin/python /srv/bank-health/app/deploy/vault_secrets.py fetch`,
   then `cd /srv/bank-health/app/airflow && docker compose restart` (or just deploy: it fetches too).
3. Not for the Fernet key: a new one makes stored connections unreadable unless both keys are
   given during the change (Airflow's `fernet_key` rotation); plan that separately.

Per credential: [rotation.md](rotation.md) (Snowflake key pairs, Databricks tokens).

## Troubleshooting

| You see | Do this |
|---|---|
| Containers restarting; logs say `Vault mode: missing in /run/bank-health/env: ...` | `sudo systemctl restart bank-health-secrets.service`, check its status, then `docker compose up -d` |
| `bank-health-secrets.service` failed at boot (`VAULT FETCH FAILED: could not read: ...`) | the names listed couldn't be read: check the policy, the dynamic group and that the secrets are Active; the service retries 10 times in 10 minutes |
| `DEPLOY REFUSED: could not read the secrets from OCI Vault` | same as above; nothing was changed by the failed deploy |
| A `docker compose exec` command says a secret is missing | start it with `. /opt/airflow/include/load_secrets.sh && ...` |
