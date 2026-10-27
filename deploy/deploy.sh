#!/usr/bin/env bash
# Deploy one approved commit of main to this machine (the prod VM).
#
# Called by the GitHub self-hosted runner (user gh-runner) through one sudo rule:
#     sudo -n -H -u deploy /srv/bank-health/app/deploy/deploy.sh <commit sha>
# or by hand as deploy. See docs/runbooks/deploy.md.
#
# Refuses unless: it runs as deploy; the sha is exactly the current origin/main; the checkout
# has no local changes; it can fast-forward; and no bank_health_weekly run is in progress
# (recreating the scheduler would kill running tasks). Then: build, restart, smoke check.
#
# Everything runs inside main(): bash reads the whole function before running it, so updating
# this file during the fast-forward below can't change the script mid-run.
set -euo pipefail

main() {
    local app=/srv/bank-health/app
    local sha="${1:-}"

    fail() { echo "DEPLOY REFUSED: $*" >&2; exit 1; }

    [ "$(id -un)" = "deploy" ] || fail "must run as the deploy user, not $(id -un)"
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "usage: deploy.sh <40-character commit sha>"

    cd "$app"
    git fetch --quiet origin main
    local main_sha
    main_sha="$(git rev-parse origin/main)"
    [ "$main_sha" = "$sha" ] || fail "$sha is not the current main ($main_sha); a newer deploy will follow"
    [ "$(git branch --show-current)" = "main" ] || fail "the checkout is not on main"
    if ! git diff --quiet || ! git diff --cached --quiet; then
        fail "the checkout has local changes"
    fi

    cd airflow
    # Capture first, then search: `cmd | grep -q` under pipefail can report "no match" when
    # grep exits early and cmd gets SIGPIPE.
    local services
    services="$(docker compose ps --status running --services 2>/dev/null || true)"
    if grep -qx airflow-scheduler <<<"$services"; then
        local running
        running="$(docker compose exec -T airflow-scheduler airflow dags list-runs bank_health_weekly \
            --state running -o plain 2>/dev/null | tail -n +2 | grep -v 'No data found' | grep -c . || true)"
        [ "$running" = "0" ] || fail "a bank_health_weekly run is in progress; deploy again after it finishes"
    fi

    cd "$app"
    local before
    before="$(git rev-parse --short HEAD)"
    git merge --ff-only --quiet "$sha" || fail "cannot fast-forward $before to $sha"
    echo "checkout: $before -> $(git rev-parse --short HEAD)"

    cd airflow
    docker compose build
    docker compose up -d --wait --wait-timeout 600
    # dbt packages are not in git (dbt/dbt_packages); Cosmos needs them to load the DAG.
    # Versions are pinned in dbt/package-lock.yml, so this is repeatable.
    docker compose exec -T airflow-scheduler bash -c \
        'cd /opt/airflow/dbt && /home/airflow/dbt_venv/bin/dbt deps --profiles-dir . --quiet'
    "$app/deploy/smoke_check.sh"
    echo "DEPLOYED $(git rev-parse --short HEAD)"
}

main "$@"
exit $?
