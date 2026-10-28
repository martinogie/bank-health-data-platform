#!/usr/bin/env bash
# Smoke check of the prod Airflow stack on this machine: every service healthy, the
# environment is prod, the DAG is loaded with no import errors. Read-only.
# Run as deploy (deploy.sh calls it after every deploy):
#     /srv/bank-health/app/deploy/smoke_check.sh
set -euo pipefail

main() {
    cd "$(dirname "$0")/../airflow"
    fail() { echo "SMOKE CHECK FAILED: $*" >&2; exit 1; }

    local svc health
    for svc in postgres airflow-apiserver airflow-scheduler airflow-dag-processor; do
        health="$(docker compose ps --format '{{.Health}}' "$svc" 2>/dev/null || true)"
        [ "$health" = "healthy" ] || fail "$svc is '${health:-not running}'"
    done
    echo "services: all healthy"

    docker compose exec -T airflow-scheduler bash -c 'test "${BANK_HEALTH_ENV:-}" = prod' \
        || fail "BANK_HEALTH_ENV is not prod inside the scheduler"
    docker compose logs airflow-scheduler 2>/dev/null | grep 'Bank Health Airflow:' | tail -1 \
        | grep -q 'environment=prod' || fail "the startup line does not say environment=prod"
    echo "environment: prod"

    # The DAG processor needs a minute or two after a restart (Cosmos runs dbt ls).
    local errors
    for _ in $(seq 1 20); do
        errors="$(docker compose exec -T airflow-scheduler airflow dags list-import-errors -o plain 2>/dev/null || true)"
        if ! echo "$errors" | grep -q 'No data found'; then
            fail "DAG import errors: $(echo "$errors" | head -c 800)"
        fi
        local dags
        dags="$(docker compose exec -T airflow-scheduler airflow dags list -o plain 2>/dev/null || true)"
        if grep -q '^bank_health_weekly' <<<"$dags"; then  # capture first: no SIGPIPE under pipefail
            echo "DAG: bank_health_weekly loaded, no import errors"
            echo "smoke check passed"
            return 0
        fi
        sleep 15
    done
    fail "bank_health_weekly did not appear within 5 minutes"
}

main "$@"
exit $?
