#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running. Start Docker Desktop and retry." >&2
  exit 1
fi

docker compose -f infra/docker-compose.local.yml build migrate app
docker compose -f infra/docker-compose.local.yml up -d postgres migrate
docker compose -f infra/docker-compose.local.yml run --rm app python -m unittest discover -s tests -v

echo "Docker services are up."
echo "PostgreSQL: postgresql://127.0.0.1:5432/x2_trading"
echo "Run ./scripts/docker-shell.sh for the application container."
