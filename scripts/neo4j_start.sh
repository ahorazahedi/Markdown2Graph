#!/usr/bin/env bash
set -euo pipefail

# Load .env
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

NAME="${NEO4J_CONTAINER_NAME:-neo4j}"
PASSWORD="${NEO4J_PASSWORD:-yourStrongPassword}"
HTTP_PORT="${NEO4J_HTTP_PORT:-7474}"
BOLT_PORT="${NEO4J_BOLT_PORT:-7687}"
DATA_DIR="${NEO4J_DATA_DIR:-$HOME/neo4j/data}"
LOGS_DIR="${NEO4J_LOGS_DIR:-$HOME/neo4j/logs}"
IMAGE="${NEO4J_IMAGE:-neo4j:5}"

mkdir -p "$DATA_DIR" "$LOGS_DIR"

if docker ps --format '{{.Names}}' | grep -q "^${NAME}$"; then
  echo "[neo4j] container '$NAME' already running"
  exit 0
fi

if docker ps -a --format '{{.Names}}' | grep -q "^${NAME}$"; then
  echo "[neo4j] starting existing container '$NAME'"
  docker start "$NAME" >/dev/null
else
  echo "[neo4j] running new container '$NAME' from $IMAGE"
  docker run -d \
    --name "$NAME" \
    -p "${HTTP_PORT}:7474" -p "${BOLT_PORT}:7687" \
    -e NEO4J_AUTH="neo4j/${PASSWORD}" \
    -e NEO4J_PLUGINS='["apoc"]' \
    -e NEO4J_dbms_security_procedures_unrestricted='apoc.*,gds.*' \
    -v "${DATA_DIR}:/data" \
    -v "${LOGS_DIR}:/logs" \
    "$IMAGE" >/dev/null
fi

echo "[neo4j] HTTP:  http://localhost:${HTTP_PORT}"
echo "[neo4j] Bolt:  bolt://localhost:${BOLT_PORT}"
echo "[neo4j] User:  neo4j"
echo "[neo4j] Pass:  (loaded from .env)"
