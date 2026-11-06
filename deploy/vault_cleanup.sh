#!/usr/bin/env bash
# Remove the plain-text secrets left on the VM after the switch to OCI Vault (Phase 4, PR 8).
# Run on the VM as deploy, only after a full prod run on Vault succeeded and Martins said OK:
#
#   /srv/bank-health/app/deploy/vault_cleanup.sh            # DRY RUN (default): lists what would go
#   /srv/bank-health/app/deploy/vault_cleanup.sh --apply    # does it
#
# What goes (docs/runbooks/vault.md, "Cleanup"):
#   - from secrets/.env: the 7 values now in Vault, and the Postgres-mirror/pgAdmin leftovers
#     (the mirror runs on Martins' PC only);
#   - secrets/.env.save (an editor backup), secrets/keys/snowflake_rsa_key.p8 (the unencrypted key),
#     app/airflow/.env.before-vault (the rollback copy; paths only).
# Before anything: checks that Vault mode is on, the RAM copy is complete and the stack is healthy.
# After: recreates the containers (so Docker's stored settings drop the old values), runs the smoke
# check, and checks that no Vault name is left in Docker's settings or in .env.
# Values are never printed. Files are overwritten before removal (shred); on a journaling or
# copy-on-write disk that can't guarantee old blocks are gone, but no readable copy remains.
#
# For tests: BANK_HEALTH_ROOT (default /srv/bank-health) and --no-restart (skip Docker).
set -euo pipefail

main() {
    local root="${BANK_HEALTH_ROOT:-/srv/bank-health}" apply=0 restart=1
    for arg in "$@"; do
        case "$arg" in
            --apply) apply=1 ;;
            --no-restart) restart=0 ;;
            *) echo "usage: vault_cleanup.sh [--apply] [--no-restart]" >&2; exit 2 ;;
        esac
    done
    local env_file="$root/secrets/.env" compose_env="$root/app/airflow/.env"
    local runtime="${BANK_HEALTH_RUNTIME:-/run/bank-health}"
    # Names only. The first 7 must match SECRETS in deploy/vault_secrets.py (a test checks it).
    local vault_names=(DATABRICKS_TOKEN SNOWFLAKE_PRIVATE_KEY_PASSPHRASE AIRFLOW__CORE__FERNET_KEY
        AIRFLOW__API_AUTH__JWT_SECRET _AIRFLOW_WWW_USER_PASSWORD SLACK_WEBHOOK_URL DISCORD_WEBHOOK_URL)
    local mirror_names=(POSTGRES_USER POSTGRES_PASSWORD POSTGRES_VIEWER_PASSWORD PGADMIN_EMAIL PGADMIN_PASSWORD)
    local files=("$root/secrets/.env.save" "$root/secrets/keys/snowflake_rsa_key.p8" "$root/app/airflow/.env.before-vault")

    fail() { echo "CLEANUP STOPPED: $*" >&2; exit 1; }

    # 1. Preconditions: Vault mode on, RAM copy complete
    grep -q '^BANK_HEALTH_RUNTIME_SECRETS_DIR=.' "$compose_env" || fail "Vault mode is not on in $compose_env"
    grep -q '^BANK_HEALTH_KEYS_DIR=/run/bank-health/keys$' "$compose_env" || fail "the stack still reads keys from disk"
    local n
    for n in "${vault_names[@]}"; do
        [ -s "$runtime/env/$n" ] || fail "$runtime/env/$n is missing: run vault_secrets.py fetch first"
    done
    [ -s "$runtime/keys/snowflake_rsa_key.p8" ] || fail "the Vault key copy is missing in $runtime/keys"
    if [ "$restart" = 1 ]; then
        (cd "$root/app/airflow" && docker compose ps --status running --services | grep -qx airflow-scheduler) \
            || fail "the stack isn't running"
    fi

    # 2. What would go
    local remove=()
    for n in "${vault_names[@]}" "${mirror_names[@]}"; do
        grep -q "^${n}=" "$env_file" && remove+=("$n")
    done
    echo "from $env_file: ${#remove[@]} line(s): ${remove[*]:-none}"
    local present=()
    for f in "${files[@]}"; do [ -e "$f" ] && present+=("$f"); done
    echo "files: ${present[*]:-none}"
    if [ "$apply" = 0 ]; then
        echo "DRY RUN: nothing changed. Run again with --apply."
        return 0
    fi

    # 3. Rewrite .env without those lines, then overwrite and remove the old copy
    if [ "${#remove[@]}" -gt 0 ]; then
        local pattern
        pattern="^($(IFS='|'; echo "${remove[*]}"))="
        umask 077
        grep -Ev "$pattern" "$env_file" > "$env_file.new" || true
        shred -u "$env_file"
        mv "$env_file.new" "$env_file"
        chmod 600 "$env_file"
    fi
    for f in "${present[@]}"; do shred -u "$f"; done

    # 4. Checks
    for n in "${vault_names[@]}" "${mirror_names[@]}"; do
        grep -q "^${n}=" "$env_file" && fail "$n is still in $env_file"
    done
    for f in "${files[@]}"; do [ -e "$f" ] && fail "$f still exists"; done
    if [ "$restart" = 1 ]; then
        (cd "$root/app/airflow" && docker compose up -d --wait --wait-timeout 600 >/dev/null)
        "$root/app/deploy/smoke_check.sh"
        local left
        left="$(docker inspect airflow-airflow-scheduler-1 --format '{{range .Config.Env}}{{println .}}{{end}}' \
            | cut -d= -f1 | grep -cxE "$(IFS='|'; echo "${vault_names[*]}")" || true)"
        [ "$left" = "0" ] || fail "$left Vault name(s) still in Docker's container settings"
        echo "docker inspect: 0 Vault names in the container settings"
    fi
    echo "CLEANED: ${#remove[@]} line(s) and ${#present[@]} file(s) removed; Vault is now the only copy."
}

main "$@"
