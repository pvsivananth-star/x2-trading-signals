#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Desktop and retry." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running. Start Docker Desktop and retry." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for local health checks." >&2
  exit 1
fi

compose=(docker compose -f infra/docker-compose.local.yml)
mkdir -p logs

"${compose[@]}" build postgres migrate app api mock-broker
"${compose[@]}" up -d postgres

postgres_ready=false
for _ in $(seq 1 30); do
  if "${compose[@]}" exec -T postgres pg_isready -U x2_admin -d x2_trading >/dev/null 2>&1; then
    postgres_ready=true
    break
  fi
  sleep 2
done
if [[ "$postgres_ready" != true ]]; then
  echo "PostgreSQL did not become healthy within 60 seconds." >&2
  "${compose[@]}" logs postgres >&2 || true
  exit 1
fi

"${compose[@]}" up --abort-on-container-exit --exit-code-from migrate migrate

# The tools profile starts API and broker with loopback-only host port bindings.
"${compose[@]}" --profile tools up -d api mock-broker

api_ready=false
broker_ready=false
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error http://127.0.0.1:8080/v1/health >/dev/null 2>&1; then api_ready=true; fi
  if curl --fail --silent --show-error http://127.0.0.1:9090/health >/dev/null 2>&1; then broker_ready=true; fi
  if [[ "$api_ready" == true && "$broker_ready" == true ]]; then break; fi
  sleep 2
done

if [[ "$api_ready" != true || "$broker_ready" != true ]]; then
  echo "One or more application services failed their health check." >&2
  [[ "$api_ready" != true ]] && "${compose[@]}" logs api >&2 || true
  [[ "$broker_ready" != true ]] && "${compose[@]}" logs mock-broker >&2 || true
  exit 1
fi

echo "Local stack started."
echo "PostgreSQL: postgresql://127.0.0.1:5432/x2_trading"
echo "Main API:  http://127.0.0.1:8080"
echo "Dashboard: http://127.0.0.1:8080/v1/dashboard"
echo "Mock broker: http://127.0.0.1:9090"
echo "Logs: ./logs/x2.log"
echo "Stop: ./scripts/stop-local.sh"
