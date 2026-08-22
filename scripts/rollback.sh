#!/usr/bin/env bash
# rollback.sh — Rollback GPCG to a previous deploy tag
#
# Usage:
#   ./scripts/rollback.sh                    # rollback to last pre-deploy tag
#   ./scripts/rollback.sh pre-deploy-20260821-223659  # rollback to specific tag
#   ./scripts/rollback.sh v0.3.14            # rollback to specific version
#
# What it does:
#   1. Checks out the specified tag (or the latest pre-deploy tag)
#   2. Deploys with --no-build --no-commit --no-test (uses existing images)
#   3. Prints recovery instructions
#
# IMPORTANT: This does NOT rollback database migrations. If a deploy
# included schema changes, manual DB rollback may be needed.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

TAG="${1:-}"

if [[ -z "$TAG" ]]; then
  # Find the latest pre-deploy tag
  TAG=$(git tag -l "pre-deploy-*" --sort=-creatordate | head -1)
  if [[ -z "$TAG" ]]; then
    echo "No pre-deploy tags found. Nothing to rollback to."
    exit 1
  fi
  echo "Rolling back to latest tag: $TAG"
else
  # Verify tag exists
  if ! git tag -l "$TAG" | grep -q .; then
    echo "Tag not found: $TAG"
    echo "Available tags:"
    git tag -l "pre-deploy-*" --sort=-creatordate | head -10
    git tag -l "v*" --sort=-creatordate | head -10
    exit 1
  fi
fi

echo "→ Checking out $TAG..."
git checkout "$TAG"

echo "→ Deploying (no build, no commit, no test)..."
./scripts/deploy.sh --no-build --no-commit --no-test

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Rollback concluído: $TAG"
echo "  URL: https://brunointegrations.com/gpcg/"
echo ""
echo "  To return to the latest code:"
echo "    git checkout feature/multi-worker"
echo "═══════════════════════════════════════════════════════════════"
