# Runbook: rotating credentials

The weekly `check_credentials` task warns 14 days before a credential is due (EXPIRING message in
both channels). Dates live in [ops/credentials.yml](../../ops/credentials.yml) (no secret values);
Databricks token expiry comes from the token API. **You handle every secret value yourself.**
After a rotation, update the date in `ops/credentials.yml` in a pull request.

More secrets (Airflow keys, passwords, webhooks, OCI) are added here with Phase 4, PR 8 (OCI Vault).

## Snowflake key pairs

Every 180 days, per service user (`SVC_BANK_PIPELINE`, `SVC_BANK_DEV`, `SVC_BANK_CI`, `SVC_POWERBI`).
Snowflake holds two public-key slots, so a rotation has no downtime:

1. On the machine that uses the key, make a new key pair (private key encrypted):
   `openssl genrsa 2048 | openssl pkcs8 -topk8 -v2 aes256 -inform PEM -out new_key.p8`
   then the public key: `openssl rsa -in new_key.p8 -pubout -out new_key.pub`.
2. In Snowsight, as ACCOUNTADMIN: `ALTER USER <user> SET RSA_PUBLIC_KEY_2 = '<public key, without the BEGIN/END lines>';`
3. Point the user's client at the new private key (and its passphrase): the VM's secrets for the
   pipeline, the PC's `keys/` for dev and Power BI, the GitHub secret `SNOWFLAKE_CI_PRIVATE_KEY` for CI.
4. Check a login works with the new key (a manual run, the CI workflow, a Power BI refresh).
5. Remove the old key: `ALTER USER <user> UNSET RSA_PUBLIC_KEY;` (next time, rotate into slot 1 and
   unset slot 2). Delete the old private key file.
6. Update `set_on` for that user in `ops/credentials.yml`.

## Databricks tokens

The pipeline's token (on the VM) and the GitHub bundle-deploy token. Both are checked from the API.

1. Databricks → Settings → Developer → Access tokens → **Generate new token**, with an expiry
   (90 days for the deploy token) and a comment naming its use.
2. Put it where it's used: the VM's secrets (`DATABRICKS_TOKEN`) or the GitHub `production`
   environment secret `DATABRICKS_TOKEN_DEPLOY`.
3. Check it works: a manual run (VM) or a deploy (GitHub).
4. Revoke the old token in the same page.
