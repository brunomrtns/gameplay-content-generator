#!/usr/bin/env bash

set -euo pipefail

# ===== CONFIG =====
# Path to the GPCG project venv (where textual is installed)
GPCG_PROJECT_DIR="${GPCG_PROJECT_DIR:-/home/bruno/Desenvolvimento/brunointegrations/gameplay-content-generator}"
GPCG_VENV="${GPCG_VENV:-${GPCG_PROJECT_DIR}/.venv/bin/python}"

# ===== LAUNCH =====
if [[ ! -f "${GPCG_VENV}" ]]; then
  echo "ERROR: GPCG venv not found at ${GPCG_VENV}" >&2
  echo "       Set GPCG_VENV env var to point to the correct python." >&2
  exit 1
fi

# The worker_panel module is installed in the project's venv
# (added via `pip install -e .` or available via PYTHONPATH)
exec "${GPCG_VENV}" -m gpcg.cli.main panel "$@"
