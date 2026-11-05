# Sourced (not run) by entrypoint.sh, by airflow-init, and by hand before `docker compose exec`
# commands that need a secret:  . /opt/airflow/include/load_secrets.sh
#
# Vault mode (prod after the PR 8 switch): BANK_HEALTH_SECRETS_DIR is set by docker-compose.yaml
# when the host has BANK_HEALTH_RUNTIME_SECRETS_DIR. Each secret is a file in $DIR/env/ (written
# into RAM by deploy/vault_secrets.py) and is exported here, overriding any value from the .env
# file. All of them must be present, or the container stops: no half-configured prod.
#
# .env mode (dev, and prod before the switch or after a rollback): BANK_HEALTH_SECRETS_DIR is
# empty and nothing happens; the values come from the env_file as before.
#
# The names must match SECRETS in deploy/vault_secrets.py (a unit test checks that).
BANK_HEALTH_VAULT_ENV_NAMES="DATABRICKS_TOKEN SNOWFLAKE_PRIVATE_KEY_PASSPHRASE AIRFLOW__CORE__FERNET_KEY AIRFLOW__API_AUTH__JWT_SECRET _AIRFLOW_WWW_USER_PASSWORD SLACK_WEBHOOK_URL DISCORD_WEBHOOK_URL"

if [ -n "${BANK_HEALTH_SECRETS_DIR:-}" ]; then
  _bh_missing=""
  for _bh_name in $BANK_HEALTH_VAULT_ENV_NAMES; do
    _bh_file="$BANK_HEALTH_SECRETS_DIR/env/$_bh_name"
    if [ -s "$_bh_file" ]; then
      export "$_bh_name=$(cat "$_bh_file")"
    else
      _bh_missing="$_bh_missing $_bh_name"
    fi
  done
  if [ -n "$_bh_missing" ]; then
    echo "Vault mode: missing in $BANK_HEALTH_SECRETS_DIR/env:$_bh_missing. Run deploy/vault_secrets.py fetch on the host. Not starting." >&2
    exit 1
  fi
  unset _bh_missing _bh_name _bh_file
fi
