#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
BACKEND_DIR="$ROOT/backend"
VENV_DIR="${BACKEND_VENV:-$BACKEND_DIR/.venv}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

PORT="${FLASK_PORT:-8000}"
HOST="${FLASK_HOST:-0.0.0.0}"

cd "$BACKEND_DIR"

# Create venv on first run
if [[ ! -d "$VENV_DIR" ]]; then
  echo "[backend] creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Install / update deps if requirements.txt is newer than the install marker
MARKER="$VENV_DIR/.deps-installed"
if [[ ! -f "$MARKER" || "$BACKEND_DIR/requirements.txt" -nt "$MARKER" ]]; then
  echo "[backend] installing requirements"
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  touch "$MARKER"
fi

if [[ "${1:-}" == "--prod" ]]; then
  echo "[backend] gunicorn on http://$HOST:$PORT"
  exec gunicorn -w "${BACKEND_WORKERS:-2}" -b "$HOST:$PORT" "app.wsgi:app"
else
  echo "[backend] flask dev on http://$HOST:$PORT"
  exec python -m app.wsgi
fi
