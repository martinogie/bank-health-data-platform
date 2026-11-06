#!/usr/bin/env bash
# Prints Airflow's metadata-database address (AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD runs this).
# A command rather than a fixed value, so every Airflow process gets the same answer: the
# scheduler, health checks and the deploy's `docker compose exec` commands alike.
#
# - Default (dev, and prod until the switch): the original password, `airflow`. The database is
#   reachable only inside Docker's own network.
# - BANK_HEALTH_DB_PASSWORD_FROM_VAULT=1 (set on the VM by deploy/airflow_db_password.sh --apply,
#   after the database itself was given the new password): the password from Vault
#   (prod-airflow-db-password), read from /run/bank-health/env/AIRFLOW_DB_PASSWORD.
# The password must be letters and digits only (it goes into a URL). Never printed elsewhere.
set -euo pipefail

password=airflow
if [ "${BANK_HEALTH_DB_PASSWORD_FROM_VAULT:-0}" = "1" ]; then
  file="${BANK_HEALTH_SECRETS_DIR:-/run/bank-health}/env/AIRFLOW_DB_PASSWORD"
  if [ ! -s "$file" ]; then
    echo "db_conn.sh: $file is missing (BANK_HEALTH_DB_PASSWORD_FROM_VAULT=1)" >&2
    exit 1
  fi
  password="$(cat "$file")"
  case "$password" in
    *[!A-Za-z0-9]*) echo "db_conn.sh: the database password must be letters and digits only" >&2; exit 1 ;;
  esac
fi
printf 'postgresql+psycopg2://airflow:%s@postgres/airflow' "$password"
