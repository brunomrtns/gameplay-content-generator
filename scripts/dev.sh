#!/usr/bin/env bash
# =============================================================================
# dev.sh — Development helper for gameplay-content-generator
#
# Usage:
#   ./scripts/dev.sh setup    # create venv, install deps, build frontend
#   ./scripts/dev.sh run      # run API + frontend concurrently
#   ./scripts/dev.sh worker   # run the background worker
#   ./scripts/dev.sh db       # init database
#   ./scripts/dev.sh scan     # one-shot inbox scan
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"

setup() {
    echo "→ Creating Python venv..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip wheel
    "$VENV/bin/pip" install -e ".[dev]"

    echo "→ Installing frontend deps..."
    if [ -f frontend/package.json ]; then
        (cd frontend && npm install)
    fi
    echo "✓ Setup complete."
}

run() {
    exec "$VENV/bin/gpcg" dev
}

worker() {
    exec "$VENV/bin/gpcg" worker
}

db() {
    exec "$VENV/bin/gpcg" db-init
}

scan() {
    exec "$VENV/bin/gpcg" inbox-scan
}

case "${1:-}" in
    setup) setup ;;
    run) run ;;
    worker) worker ;;
    db) db ;;
    scan) scan ;;
    *) echo "Usage: $0 {setup|run|worker|db|scan}"; exit 1 ;;
esac
