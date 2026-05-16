#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }
NAME="${NEO4J_CONTAINER_NAME:-neo4j}"
docker logs -f "$NAME"
