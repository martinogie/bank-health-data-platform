#!/usr/bin/env bash
# Check the environment, build Airflow connections from .env values, then hand over to the
# official entrypoint.
set -euo pipefail

# BANK_HEALTH_ENV is required (docker-compose.yaml sets prod, docker-compose.dev.yaml sets dev).
case "${BANK_HEALTH_ENV:-}" in
  dev|prod) ;;
  *)
    echo "BANK_HEALTH_ENV must be dev or prod for the Airflow stack, got '${BANK_HEALTH_ENV:-<not set>}'." >&2
    echo "It is set in the Compose file; there is deliberately no default. Not starting." >&2
    exit 1
    ;;
esac
# Also stops if ../.env belongs to another environment (e.g. dev credentials under prod).
python -c "from ingestion.environment import describe; print('Bank Health Airflow:', describe())"

eval "$(python /opt/airflow/include/connections_from_env.py)"
exec /entrypoint "$@"
