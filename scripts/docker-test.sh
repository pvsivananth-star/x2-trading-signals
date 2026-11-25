#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

compose=(docker compose -f infra/docker-compose.local.yml)

"${compose[@]}" up -d postgres
docker compose -f infra/docker-compose.local.yml build migrate app
"${compose[@]}" up --abort-on-container-exit --exit-code-from migrate migrate

"${compose[@]}" run --rm app python -m unittest discover -s tests -v

echo "Container unit tests and migrations passed."
