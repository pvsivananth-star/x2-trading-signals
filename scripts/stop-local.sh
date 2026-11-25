#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose -f infra/docker-compose.local.yml --profile tools down
echo "Local stack stopped. PostgreSQL data volume was preserved."
