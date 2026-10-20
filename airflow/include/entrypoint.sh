#!/usr/bin/env bash
# Build Airflow connections from .env values, then hand over to the official entrypoint.
set -euo pipefail
eval "$(python /opt/airflow/include/connections_from_env.py)"
exec /entrypoint "$@"
