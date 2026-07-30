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
my-vps "cd $VPS_DIR && docker compose -f docker-compose.prod.yml build --no-cache && docker compose -f docker-compose.prod.yml up -d"

# ── Step 3: Wait for health check ─────────────────────────────────────────────
echo "⏳ Waiting for health check..."
sleep 10
my-vps "docker inspect --format='{{.State.Health.Status}}' gpcg-api 2>/dev/null || echo 'starting'"

# ── Step 4: Update nginx config ───────────────────────────────────────────────
echo "🌐 Updating nginx configuration..."
my-vps "cat > /tmp/gpcg-nginx.conf << 'NGINX_EOF'

    # ── GPCG Upstream ─────────────────────────────────────────────────────
    upstream gpcg_api {
        server gpcg-api:8787;
        keepalive 16;
    }

NGINX_EOF

# Check if gpcg upstream already exists in nginx.conf
if ! grep -q 'gpcg_api' /opt/trivestia/nginx/nginx.conf; then
    # Add upstream before the log_format line
    sed -i '/log_format main/i\\    # ── GPCG Upstream ─────────────────────────────────────────────────────\n    upstream gpcg_api {\n        server gpcg-api:8787;\n        keepalive 16;\n    }\n' /opt/trivestia/nginx/nginx.conf
fi

# Check if gpcg location already exists
if ! grep -q 'location /gpcg' /opt/trivestia/nginx/nginx.conf; then
    # Add location block before the closing brace of the HTTPS server
    sed -i '/# ── Default location/i\\    # ── GPCG (Gameplay Content Generator) ────────────────────────────────\n    location /gpcg/ {\n        limit_req zone=api_limit burst=30 nodelay;\n        rewrite ^/gpcg/(.*)\$ /\$1 break;\n        proxy_pass         http://gpcg_api;\n        proxy_http_version 1.1;\n        proxy_set_header   Host              \$host;\n        proxy_set_header   X-Real-IP         \$remote_addr;\n        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;\n        proxy_set_header   X-Forwarded-Proto \$scheme;\n        proxy_set_header   Connection        \"\";\n        proxy_buffering    off;\n        proxy_read_timeout 300s;\n        proxy_send_timeout 300s;\n    }\n' /opt/trivestia/nginx/nginx.conf
fi
"

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
