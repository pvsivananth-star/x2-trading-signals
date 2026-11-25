#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

read -r -p "Destroy the local PostgreSQL volume and all local tick data? [y/N] " answer
if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
  echo "Reset cancelled."
  exit 0
fi

docker compose -f infra/docker-compose.local.yml down -v --remove-orphans
rm -rf data

echo "Local database and data directory reset."
