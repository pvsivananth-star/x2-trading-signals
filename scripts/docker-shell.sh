#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose -f infra/docker-compose.local.yml run --rm app /bin/bash
