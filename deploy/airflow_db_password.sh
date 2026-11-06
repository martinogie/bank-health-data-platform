#!/usr/bin/env bash
# Switch Airflow's metadata database from the default password to the Vault one (prod-airflow-db-
# password), or back. Run on the VM as deploy, not on a Monday morning, with no run in progress:
#
#   /srv/bank-health/app/deploy/airflow_db_password.sh             # DRY RUN: checks, changes nothing
#   /srv/bank-health/app/deploy/airflow_db_password.sh --apply     # database + Airflow -> Vault password
#   /srv/bank-health/app/deploy/airflow_db_password.sh --rollback  # both back to the default
#
# --apply: 1) sets the database user's password to the Vault value (sent over stdin, never on a
# command line or in a log); 2) adds BANK_HEALTH_DB_PASSWORD_FROM_VAULT=1 to airflow/.env;
# 3) recreates the Airflow containers, which then read the password through include/db_conn.sh;
# 4) smoke check. Airflow can't reach its database for the few seconds between 1 and 3; nothing
# runs then (the script checks), and the containers' restart policy covers the gap.
# --rollback: the same steps in reverse. See docs/runbooks/vault.md.
set -euo pipefail

main() {
    local app=/srv/bank-health/app mode=dry
    case "${1:-}" in
        "") ;;
        --apply) mode=apply ;;
        --rollback) mode=rollback ;;
        *) echo "usage: airflow_db_password.sh [--apply|--rollback]" >&2; exit 2 ;;
    esac
    local compose_env="$app/airflow/.env" pwfile=/run/bank-health/env/AIRFLOW_DB_PASSWORD
    fail() { echo "DB PASSWORD: STOPPED: $*" >&2; exit 1; }

    [ "$(id -un)" = deploy ] || fail "run as deploy"
    grep -q '^BANK_HEALTH_RUNTIME_SECRETS_DIR=.' "$compose_env" || fail "Vault mode is not on"
    cd "$app/airflow"
    local running
    running="$(docker compose exec -T airflow-scheduler airflow dags list-runs bank_health_weekly \
        --state running -o plain 2>/dev/null | tail -n +2 | grep -v 'No data found' | grep -c . || true)"
    [ "$running" = "0" ] || fail "a bank_health_weekly run is in progress"
    local on=0
    grep -q '^BANK_HEALTH_DB_PASSWORD_FROM_VAULT=1$' "$compose_env" && on=1
    echo "now: Airflow uses the $([ $on = 1 ] && echo Vault || echo default) database password"

    set_db_password() {  # $1 = the password, sent over stdin to psql inside the postgres container
        printf "ALTER USER airflow WITH PASSWORD '%s';\n" "$1" \
            | docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U airflow -d airflow >/dev/null
    }

    case "$mode" in
        dry)
            [ -s "$pwfile" ] || fail "$pwfile is missing: run vault_secrets.py fetch (prod-airflow-db-password in Vault?)"
            grep -qE '^[A-Za-z0-9]{24,}$' "$pwfile" || fail "the Vault password must be 24+ letters and digits only"
            echo "DRY RUN: the Vault password is present and well formed. Run with --apply to switch."
            ;;
        apply)
            [ "$on" = 0 ] || fail "already switched"
            grep -qE '^[A-Za-z0-9]{24,}$' "$pwfile" || fail "the Vault password must be 24+ letters and digits only"
            set_db_password "$(cat "$pwfile")"
            echo "BANK_HEALTH_DB_PASSWORD_FROM_VAULT=1" >> "$compose_env"
            docker compose up -d --wait --wait-timeout 600 >/dev/null
            "$app/deploy/smoke_check.sh"
            echo "SWITCHED: the database and Airflow use the Vault password"
            ;;
        rollback)
            [ "$on" = 1 ] || fail "not switched"
            set_db_password airflow
            sed -i '/^BANK_HEALTH_DB_PASSWORD_FROM_VAULT=1$/d' "$compose_env"
            docker compose up -d --wait --wait-timeout 600 >/dev/null
            "$app/deploy/smoke_check.sh"
            echo "ROLLED BACK: the database and Airflow use the default password again"
            ;;
    esac
}

main "$@"
