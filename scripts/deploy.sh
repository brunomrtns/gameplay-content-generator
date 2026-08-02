#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Deploy GPCG to VPS
#
# Usage:  ./scripts/deploy.sh
#
# Steps:
#   1. Rsync project to VPS (/opt/gpcg/)
#   2. Build and start Docker container
#   3. Update nginx config (add /gpcg/ location)
#   4. Reload nginx
# =============================================================================

set -euo pipefail

VPS_DIR="/opt/gpcg"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "🚀 Deploying GPCG to VPS..."

# ── Step 1: Sync project files ────────────────────────────────────────────────
echo "📦 Syncing project files to VPS..."
my-vps --rsync "$PROJECT_DIR/" "$VPS_DIR/" \
  --rsync-args "--exclude=node_modules --exclude=.venv --exclude=__pycache__ --exclude=.git --exclude=data --exclude=.env --exclude=*.pyc"

# ── Step 2: Build and start Docker container ─────────────────────────────────
echo "🔨 Building and starting Docker container..."
# Ensure the bi-net external network exists (for BI Identity Service communication)
my-vps "docker network inspect bi-net >/dev/null 2>&1 || docker network create bi-net"
my-vps "cd $VPS_DIR && docker compose -f docker-compose.prod.yml build --no-cache && docker compose -f docker-compose.prod.yml up -d"

# ── Step 3: Wait for health check ─────────────────────────────────────────────
echo "⏳ Waiting for health check..."
sleep 10
my-vps "docker inspect --format='{{.State.Health.Status}}' gpcg-api 2>/dev/null || echo 'starting'"

# ── Step 4: Update nginx config ───────────────────────────────────────────────
echo "🌐 Updating nginx configuration..."
my-vps 'python3 << "PYEOF"
import re

CONF_PATH = "/opt/trivestia/nginx/nginx.conf"
with open(CONF_PATH) as f:
    content = f.read()

# ── Ensure upstream gpcg_api exists ──────────────────────────────────────
upstream_block = """    # ── GPCG Upstream ─────────────────────────────────────────────────────
    upstream gpcg_api {
        server gpcg-api:8787;
        keepalive 16;
    }
"""
if "upstream gpcg_api" not in content:
    content = content.replace("    log_format main", upstream_block + "\n    log_format main", 1)

# ── Replace (or add) the /gpcg/ location block ───────────────────────────
# Always rewrite the full block so config stays consistent across deploys.
location_block = """    # ── GPCG (Gameplay Content Generator) ────────────────────────────────
    location /gpcg/ {
        limit_req zone=api_limit burst=30 nodelay;
        rewrite ^/gpcg/(.*)$ /$1 break;
        proxy_pass         http://gpcg_api;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Connection        "";
        proxy_buffering    off;
        proxy_read_timeout 1200s;
        proxy_send_timeout 1200s;
        client_max_body_size 1024m;
    }
"""

# Try to replace existing block (from "# ── GPCG" comment to the closing "}")
pattern = re.compile(
    r"    # ── GPCG \(Gameplay Content Generator\).*?    \}\n",
    re.DOTALL,
)
if pattern.search(content):
    content = pattern.sub(location_block, content, count=1)
elif "location /gpcg/" in content:
    # Fallback: replace from "location /gpcg/" to the first closing "}"
    pattern2 = re.compile(r"    location /gpcg/.*?    \}\n", re.DOTALL)
    content = pattern2.sub(location_block, content, count=1)
else:
    # Add before the default location block
    content = content.replace(
        "    # ── Default location",
        location_block + "\n    # ── Default location",
        1,
    )

with open(CONF_PATH, "w") as f:
    f.write(content)

print("nginx config updated")
PYEOF
'

# ── Step 5: Test and reload nginx ─────────────────────────────────────────────
echo "🔄 Testing and reloading nginx..."
my-vps "docker exec trivestia-nginx nginx -t && docker exec trivestia-nginx nginx -s reload"

# ── Step 6: Verify ────────────────────────────────────────────────────────────
echo "✅ Verifying deployment..."
my-vps "curl -s http://gpcg-api:8787/api/health 2>/dev/null || echo 'API not responding yet'"

echo ""
echo "🎉 GPCG deployed successfully!"
echo "   URL: https://brunointegrations.com/gpcg/"
echo "   API: https://brunointegrations.com/gpcg/api/health"
echo ""
echo "📋 Post-deploy checklist:"
echo "   1. Set GPCG_WORKER_API_KEY in /opt/gpcg/.env (if not already set)"
echo "   2. Restart the container: my-vps 'cd /opt/gpcg && docker compose -f docker-compose.prod.yml restart'"
echo "   3. On local PC: copy the same GPCG_WORKER_API_KEY to ~/.config/systemd/user/gpcg-worker.service"
echo "   4. Start the worker: systemctl --user start gpcg-worker"
echo "   5. Verify worker registration: curl -s https://brunointegrations.com/gpcg/api/workers"
