#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
if [[ ! -f "$ROOT/.env" || ! -f "$ROOT/.docker-elk/docker-compose.yml" ]]; then
    echo "Error: run ./setup.sh before starting the demo." >&2
    exit 1
fi

export DEMO_ROOT="$ROOT"
unset ELASTIC_VERSION ELASTIC_PASSWORD JWT_HMAC_KEY
exec docker compose --project-name "${COMPOSE_PROJECT_NAME:-elastic-auto-role-demo}" \
    --env-file "$ROOT/.env" \
    -f "$ROOT/.docker-elk/docker-compose.yml" \
    -f "$ROOT/docker-compose.demo.yml" "$@"
