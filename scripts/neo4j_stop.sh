#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

NAME="${NEO4J_CONTAINER_NAME:-neo4j}"

if docker ps --format '{{.Names}}' | grep -q "^${NAME}$"; then
  echo "[neo4j] stopping '$NAME'"
  docker stop "$NAME" >/dev/null
  echo "[neo4j] stopped"
else
  echo "[neo4j] container '$NAME' is not running"
fi
