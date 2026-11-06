#!/usr/bin/env bash
# Set the Airflow admin user's password to the current Vault value (prod-airflow-admin-password).
# Run on the VM as deploy after adding a new version of that secret in the OCI console:
#
#   /srv/bank-health/app/deploy/airflow_admin_reset.sh
#
# Steps: read the Vault into RAM (vault_secrets.py fetch); check the password is long enough;
# reset it in Airflow (inside the scheduler container; the password comes from the RAM copy, not
# from this command line); check that an API login with it works. Never prints the password.
# Rollback: in the console, make the previous secret version current again (or add a version
# with the old value), then run this script again.
set -euo pipefail

app=/srv/bank-health/app
fail() { echo "ADMIN PASSWORD: STOPPED: $*" >&2; exit 1; }
[ "$(id -un)" = deploy ] || fail "run as deploy"

/srv/bank-health/venv/bin/python "$app/deploy/vault_secrets.py" fetch >/dev/null
len="$(wc -c < /run/bank-health/env/_AIRFLOW_WWW_USER_PASSWORD)"
[ "$len" -ge 16 ] || fail "the Vault admin password has $len characters; use at least 16"

cd "$app/airflow"
docker compose exec -T airflow-scheduler bash -c '
    set -e
    . /opt/airflow/include/load_secrets.sh
    airflow users reset-password --username "${_AIRFLOW_WWW_USER_USERNAME:-admin}" \
        --password "$_AIRFLOW_WWW_USER_PASSWORD" >/dev/null
    python - <<PY
import os, requests
r = requests.post("http://airflow-apiserver:8080/auth/token", timeout=30,
                  json={"username": os.environ.get("_AIRFLOW_WWW_USER_USERNAME", "admin"),
                        "password": os.environ["_AIRFLOW_WWW_USER_PASSWORD"]})
print("API login with the new password: HTTP", r.status_code)
raise SystemExit(0 if r.status_code in (200, 201) else 1)
PY
'
echo "RESET: the admin password is the Vault value ($len characters)"
