#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$ROOT/frontend"

cd "$FRONTEND_DIR"

if ! command -v node >/dev/null 2>&1; then
  echo "[frontend] node not found — install Node 18+ first" >&2
  exit 1
fi

PKG_MGR="npm"
if command -v pnpm >/dev/null 2>&1 && [[ -f "pnpm-lock.yaml" ]]; then
  PKG_MGR="pnpm"
elif command -v yarn >/dev/null 2>&1 && [[ -f "yarn.lock" ]]; then
  PKG_MGR="yarn"
fi

if [[ ! -d "node_modules" || "package.json" -nt "node_modules/.package-lock.json" ]]; then
  echo "[frontend] installing deps with $PKG_MGR"
  if [[ "$PKG_MGR" == "npm" ]]; then
    npm install --silent
  else
    "$PKG_MGR" install
  fi
fi

if [[ "${1:-}" == "--build" ]]; then
  echo "[frontend] building production bundle"
  "$PKG_MGR" run build
  echo "[frontend] preview on http://localhost:4173"
  exec "$PKG_MGR" run preview -- --host
else
  echo "[frontend] vite dev on http://localhost:5173 (proxy /api -> :${FLASK_PORT:-8000})"
  exec "$PKG_MGR" run dev -- --host
fi
