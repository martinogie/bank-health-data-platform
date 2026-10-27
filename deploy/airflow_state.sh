#!/usr/bin/env bash
# Print the Airflow state that cutover carries over, on this machine, so the two machines can be
# compared side by side (docs/runbooks/cutover_and_rollback.md). Read-only. Values printed are
# not secrets: watermarks (dates), run ids and counts. The Cosmos cache is shown by size only.
#
# Run from the airflow/ folder of the checkout (Git Bash on the PC, bash on the VM):
#     ../deploy/airflow_state.sh
set -euo pipefail

main() {
    local af=(docker compose exec -T airflow-scheduler airflow)
    echo "== Airflow state on $(hostname) at $(date -u +%Y-%m-%dT%H:%MZ)"
    echo "-- watermarks (Variables)"
    for var in fdic_last_repdte cfpb_last_date_received; do
        printf '  %-26s %s\n' "$var" "$("${af[@]}" variables get "$var" 2>/dev/null || echo '(missing)')"
    done
    local cache_bytes
    cache_bytes="$("${af[@]}" variables get cosmos_cache__bank_health_weekly__dbt_build 2>/dev/null | wc -c || true)"
    printf '  %-26s %s bytes (rebuilt automatically if missing)\n' "cosmos dbt cache" "$cache_bytes"

    echo "-- DAGs"
    for dag in bank_health_weekly bank_health_slo_check; do
        local paused runs last_ok
        paused="$("${af[@]}" dags details "$dag" -o plain 2>/dev/null | awk '$1=="is_paused"{print $2}' || true)"
        runs="$("${af[@]}" dags list-runs "$dag" -o plain 2>/dev/null | tail -n +2 | grep -vc 'No data found' || true)"
        last_ok="$("${af[@]}" dags list-runs "$dag" --state success -o plain 2>/dev/null | awk 'NR==2{print $2}' || true)"
        printf '  %-24s paused=%-5s runs=%-4s last success=%s\n' "$dag" "${paused:-?}" "$runs" "${last_ok:-none}"
    done
    local running
    running="$("${af[@]}" dags list-runs bank_health_weekly --state running -o plain 2>/dev/null | tail -n +2 | grep -vc 'No data found' || true)"
    echo "-- runs in progress: $running"
    echo "-- connections stored in the database: $("${af[@]}" connections list -o plain 2>/dev/null | tail -n +2 | grep -vc 'No data found' || true) (expected 0: they come from .env)"
}

main "$@"
exit $?
