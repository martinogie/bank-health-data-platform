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
| `prod-airflow-db-password` | `/run/bank-health/env/AIRFLOW_DB_PASSWORD` | Airflow's own metadata database, through `airflow/include/db_conn.sh`, once switched (below) |

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
  VM; their copies on the VM were removed in the cleanup (6 Nov 2026).

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

# 5. Check: the files exist (names only), and Vault equals what the running Airflow uses
sudo ls /run/bank-health/env /run/bank-health/keys
sudo -u deploy -H /srv/bank-health/venv/bin/python /srv/bank-health/app/deploy/vault_secrets.py check-running
```

Step 5 compares fingerprints (never values) of each Vault secret with the variable in the running
scheduler. Before the switch it must show `matches` for the 6 copied values (the passphrase is new,
so it differs until the switch); after the switch, all 7 match. A `DIFFERS` on a copied value means
a copy-paste slip: fix that secret in the console (new version), then
`sudo systemctl restart bank-health-secrets.service` and check again. (It replaced `check-env`,
which compared with the `.env` file and misread a line with text after the value, 5 Nov 2026.)

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
`/srv/bank-health/secrets/keys/snowflake_rsa_key.p8`; and `.env.before-vault`. After it, Vault is
the only copy (plus Martins' password manager), and the rollback above no longer applies.

`deploy/vault_cleanup.sh` does it, as deploy (not on a Monday morning; no run in progress):

```bash
ssh bank-health-vm
/srv/bank-health/app/deploy/vault_cleanup.sh            # dry run: lists what would go, changes nothing
/srv/bank-health/app/deploy/vault_cleanup.sh --apply    # only with Martins' OK
```

It stops unless Vault mode is on and the RAM copy is complete. With `--apply` it rewrites `.env`
without those lines, overwrites and removes the old copies (`shred`), recreates the containers,
runs the smoke check and checks that Docker's stored container settings hold none of the 7 Vault
names. (On a journaling or copy-on-write disk, `shred` can't guarantee every old block is gone, but
no readable copy remains.) Then confirm with `vault_secrets.py check-running`: all 7 match.

## Airflow's database password and the admin password

Airflow's own metadata database (run history, variables) started with the default password
`airflow`; it is reachable only inside Docker's own network. Airflow now reads the database address
from a command (`airflow/include/db_conn.sh`): the default password until the switch, the Vault one
(`prod-airflow-db-password`) after it.

**Order matters:** the Vault secret `prod-airflow-db-password` must exist **before** the code that
lists it is deployed; otherwise the deploy's Vault read refuses (safely, nothing changes).

1. **Martins, in the OCI console** (Vault `bank-health-vault` → Secrets):
   - **Create Secret** `prod-airflow-db-password`, key `bank-health-secrets-key`, Plain-Text, value:
     **letters and digits only, at least 24** (it goes into a URL). For example, on the PC:
     `& "C:\Program Files\Git\usr\bin\openssl.exe" rand -hex 24`.
   - On `prod-airflow-admin-password`: **Create Secret Version** with a strong new value, **at least
     16 characters** (any characters). Keep both in the password manager.
2. Merge the PR and approve the deploy. The deploy reads the new secret into RAM; nothing else
   changes yet.
3. **The database switch** (as deploy, not on a Monday morning, no run in progress):
   ```bash
   /srv/bank-health/app/deploy/airflow_db_password.sh            # dry run: checks only
   /srv/bank-health/app/deploy/airflow_db_password.sh --apply    # database + Airflow -> Vault password
   ```
   It sets the database user's password (over stdin, never on a command line), adds
   `BANK_HEALTH_DB_PASSWORD_FROM_VAULT=1` to `airflow/.env`, recreates the containers and runs the
   smoke check. **Rollback:** `airflow_db_password.sh --rollback` (both back to `airflow`).
4. **The admin password:** `/srv/bank-health/app/deploy/airflow_admin_reset.sh` reads Vault, resets
   the admin user to the new value inside the scheduler container, and checks an API login with it.
   **Rollback:** make the previous secret version current again in the console (or add a version
   with the old value), then run the script again.

Tested in dev on 6 Nov 2026: switch off (unchanged), Vault mode, switch on with a random test
password (all healthy, the database reached with the new password), admin reset (API login 201),
rollback (default password again, all healthy).

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
