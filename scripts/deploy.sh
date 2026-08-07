#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Deploy do GPCG para a VPS
#
# Uso:  ./deploy.sh [--no-build] [--no-commit] [--auto-commit]
#                   [--bump patch|minor|major] [--no-test]
#
# O que faz:
#   0. Verifica working tree limpa (ou commita automaticamente com --auto-commit)
#   0.5. Roda testes (pytest)
#   1. Cria tag de rollback pre-deploy-TIMESTAMP
#   2. Sincroniza o código para /opt/gpcg na VPS (via rsync)
#   3. Builda as imagens Docker na VPS (api, worker)
#   4. Sobe a stack com docker compose
#   5. Atualiza nginx do trivestia-nginx com rotas do GPCG
#   6. Reinicia nginx
#   7. Smoke test (API health)
#   8. Se OK: bump de versão + commit + tag vX.Y.Z + push
#
# Versionamento (semver):
#   --bump patch  (default) — bug fixes, pequenas mudanças
#   --bump minor          — novas features, backward compatible
#   --bump major          — breaking changes
#
# Pré-requisitos:
#   - my-vps instalado e configurado
#   - Docker + Docker Compose na VPS
#   - trivestia-nginx rodando na VPS (reverse proxy principal)
#   - rede Docker bi-net existente (para BI Identity Service)
# =============================================================================

set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"
VPS_PATH="/opt/gpcg"
VENV="$PROJECT_ROOT/.venv/bin"

# ── Verificar my-vps ─────────────────────────────────────────────────────────
if ! command -v my-vps &>/dev/null; then
  echo -e "\033[1;31m  ✗\033[0m 'my-vps' não encontrado. Instale my-vps antes de fazer deploy." >&2
  exit 1
fi

# Helper: executar comando remoto na VPS via my-vps
vps() {
  my-vps --no-lock "$@"
}

log() { echo -e "\033[1;34m[deploy]\033[0m $*"; }
ok()  { echo -e "\033[1;32m  ✓\033[0m $*"; }
err() { echo -e "\033[1;31m  ✗\033[0m $*" >&2; }

# ── Argumentos ───────────────────────────────────────────────────────────────
NO_BUILD=0
NO_COMMIT=0
AUTO_COMMIT=0
BUMP="patch"
RUN_TESTS=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build)     NO_BUILD=1;    shift ;;
    --no-commit)    NO_COMMIT=1;   shift ;;
    --auto-commit)  AUTO_COMMIT=1; shift ;;
    --no-test)      RUN_TESTS=0;   shift ;;
    --bump)         BUMP="$2";     shift 2 ;;
    -h|--help)
      echo "Uso: ./deploy.sh [opções]"
      echo ""
      echo "  --no-build         Pula o build (usa imagens existentes)"
      echo "  --no-commit        Não commita/bumpa versão após deploy"
      echo "  --auto-commit      Commita mudanças não-commitadas antes do deploy"
      echo "  --no-test          Pula os testes"
      echo "  --bump patch|minor|major  Tipo de bump (default: patch)"
      exit 0
      ;;
    *) echo "Argumento desconhecido: $1"; exit 1 ;;
  esac
done

# ── Helpers de versionamento ─────────────────────────────────────────────────

get_version() {
  grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/'
}

set_version() {
  local new_version="$1"
  sed -i "s/^version = \".*\"/version = \"$new_version\"/" pyproject.toml
}

bump_version() {
  local current="$1" type="$2"
  local major minor patch
  IFS='.' read -r major minor patch <<< "$current"
  case "$type" in
    major)  major=$((major + 1)); minor=0; patch=0 ;;
    minor)  minor=$((minor + 1)); patch=0 ;;
    patch)  patch=$((patch + 1)) ;;
  esac
  echo "${major}.${minor}.${patch}"
}

# ── Step 0: Verificar working tree ───────────────────────────────────────────
log "Verificando pré-requisitos..."

if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
  if [[ "$AUTO_COMMIT" -eq 1 ]]; then

    # ── Safety: block sensitive files ──────────────────────────────────────
    SENSITIVE=$(git status --porcelain | awk '{print $2}' | grep -iE '\.env($|\.|[^.])|secret|\.pem|\.key|id_rsa|id_ed25519|credentials|\.p12|\.pfx|PLAN\.md' || true)
    if [[ -n "$SENSITIVE" ]]; then
      err "Arquivos sensíveis detectados — auto-commit abortado:"
      echo "$SENSITIVE" | sed 's/^/    /'
      echo ""
      echo "  Commite manualmente após revisar:"
      echo "    git add -A && git commit -m 'sua mensagem'"
      exit 1
    fi

    CHANGED_FILES=$(git status --porcelain | awk '{print $2}')
    CHANGES=$(echo "$CHANGED_FILES" | wc -l)
    log "Auto-commitando $CHANGES arquivo(s) modificado(s)..."
    git add -A
    git commit -m "chore: pre-deploy auto-commit"
    ok "Mudanças commitadas"
  else
    err "Working tree não está limpa. Commite suas mudanças antes do deploy."
    echo ""
    git status --short
    echo ""
    echo "  Ou use --auto-commit:"
    echo "    ./deploy.sh --auto-commit"
    exit 1
  fi
else
  ok "Working tree limpa"
fi

# Versão atual
CURRENT_VERSION=$(get_version)
log "Versão atual: v$CURRENT_VERSION"

# ── Step 0.5: Rodar testes ───────────────────────────────────────────────────
if [[ "$RUN_TESTS" -eq 1 ]]; then
  log "Rodando testes automatizados (pytest)..."
  if ! $VENV/pytest tests/ -q --tb=short 2>&1; then
    err "Testes falharam — deploy abortado"
    err "Rode '.venv/bin/pytest tests/ -q' localmente para investigar"
    exit 1
  fi
  ok "Todos os testes passaram"
fi

# Criar tag de rollback
DEPLOY_TAG="pre-deploy-$(date +%Y%m%d-%H%M%S)"
if git rev-parse --git-dir &>/dev/null; then
  git tag "$DEPLOY_TAG" 2>/dev/null && ok "Tag de rollback criada: $DEPLOY_TAG" || true
fi

# Verificar conectividade com VPS
if ! vps "echo ok" &>/dev/null; then
  err "Não foi possível conectar à VPS via my-vps"
  exit 1
fi
ok "Conexão com VPS OK (via my-vps)"

# ── Step 1: Sincronizar código ───────────────────────────────────────────────
log "Step 1/7: Sincronizando código para VPS..."

vps "mkdir -p $VPS_PATH"

RSYNC_EXCLUDES="--exclude=node_modules --exclude=.venv --exclude=__pycache__ --exclude=.git --exclude=data --exclude=.env --exclude=*.pyc --exclude=.pytest_cache --exclude=.devin --exclude=.claude --exclude='*.log'"

if command -v rsync &>/dev/null; then
  my-vps --no-lock --rsync "$PROJECT_ROOT/" "$VPS_PATH/" --rsync-args "$RSYNC_EXCLUDES" 2>&1 || {
    err "rsync falhou"
    exit 1
  }
  ok "Código sincronizado via rsync"
else
  err "rsync não disponível"
  exit 1
fi

# ── Step 2: Build das imagens ────────────────────────────────────────────────
if [[ "$NO_BUILD" -eq 0 ]]; then
  log "Step 2/7: Buildando imagens Docker na VPS (docker compose build)..."

  # Ensure the bi-net external network exists (for BI Identity Service communication)
  vps "docker network inspect bi-net >/dev/null 2>&1 || docker network create bi-net"

  BUILD_LOG=$(mktemp)
  BUILD_EXIT=0
  vps "cd $VPS_PATH && docker compose -f docker-compose.prod.yml build 2>&1; echo \"EXIT_CODE=\$?\"" > "$BUILD_LOG" 2>&1 || BUILD_EXIT=$?
  BUILD_REMOTE_EXIT=$(grep -oP 'EXIT_CODE=\K[0-9]+' "$BUILD_LOG" | tail -1)
  tail -20 "$BUILD_LOG"
  rm -f "$BUILD_LOG"
  if [[ "$BUILD_EXIT" -ne 0 || "$BUILD_REMOTE_EXIT" != "0" ]]; then
    err "docker compose build FALHOU (exit: ${BUILD_REMOTE_EXIT:-$BUILD_EXIT})"
    err "Rode manualmente para ver o log completo:"
    err "  my-vps \"cd $VPS_PATH && docker compose -f docker-compose.prod.yml build\""
    exit 1
  fi
  # Clean build cache after successful build — we don't reuse it and it
  # can accumulate hundreds of GB over time. Free the space for video storage.
  vps "docker builder prune -af 2>/dev/null" || true
  ok "Imagens buildadas (gpcg-api:latest, gpcg-worker:latest)"
else
  log "Step 2/7: Build pulado (--no-build)"
fi

# ── Step 3: Subir a stack ────────────────────────────────────────────────────
log "Step 3/7: Subindo stack com docker compose..."
COMPOSE_LOG=$(mktemp)
COMPOSE_EXIT=0
vps "cd $VPS_PATH && docker compose -f docker-compose.prod.yml up -d 2>&1; echo \"EXIT_CODE=\$?\"" > "$COMPOSE_LOG" 2>&1 || COMPOSE_EXIT=$?
COMPOSE_REMOTE_EXIT=$(grep -oP 'EXIT_CODE=\K[0-9]+' "$COMPOSE_LOG" | tail -1)
tail -20 "$COMPOSE_LOG"
rm -f "$COMPOSE_LOG"
if [[ "$COMPOSE_EXIT" -ne 0 || "$COMPOSE_REMOTE_EXIT" != "0" ]]; then
  err "docker compose up FALHOU (exit: ${COMPOSE_REMOTE_EXIT:-$COMPOSE_EXIT})"
  exit 1
fi
ok "Stack iniciada"

# ── Step 4: Atualizar nginx do trivestia-nginx ───────────────────────────────
log "Step 4/7: Atualizando nginx com rotas do GPCG..."

vps 'python3 << "PYEOF"
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

# ── Ensure upstream gpcg_catalog exists ──────────────────────────────────
# NOTE: We DO NOT use a static upstream for the catalog because nginx resolves
# upstream hostnames at config-load time. If the catalog container is
# restarting, nginx crashes. Instead, we use a resolver + variable in
# proxy_pass (see the location block below), which resolves at request time.
# So we intentionally do NOT add an upstream block for gpcg_catalog.

# ── Remove stale gpcg_catalog upstream if it was added by a previous deploy ─
content = re.sub(
    r"\n    # ── GPCG Catalog Upstream.*?    \}\n",
    "\n",
    content,
    flags=re.DOTALL,
)

# ── Replace (or add) the /gpcg/ location block ───────────────────────────
# Always rewrite the full block so config stays consistent across deploys.
location_block = """    # ── GPCG (Gameplay Content Generator) ────────────────────────────────
    # Video files — cached at nginx level for fast repeated loads
    location ~ ^/gpcg/api/videos/[0-9]+/file$ {
        limit_req zone=api_limit burst=30 nodelay;
        rewrite ^/gpcg/(.*)$ /$1 break;
        proxy_pass         http://gpcg_api;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Connection        "";
        proxy_buffering    on;
        proxy_cache_valid  200 206 1h;
        proxy_read_timeout 1200s;
        proxy_send_timeout 1200s;
        client_max_body_size 1024m;
    }
    # Thumbnails — cached longer (rarely change)
    location ~ ^/gpcg/api/videos/[0-9]+/thumbnail$ {
        limit_req zone=api_limit burst=30 nodelay;
        rewrite ^/gpcg/(.*)$ /$1 break;
        proxy_pass         http://gpcg_api;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Connection        "";
        proxy_buffering    on;
        proxy_cache_valid  200 24h;
    }
    # All other GPCG API routes
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
    # Game Catalog Service — proxied to the catalog container (port 8788).
    # Uses resolver + variable so nginx resolves at request time (not startup).
    # This prevents nginx from crashing if the catalog container is restarting.
    location /gpcg/api/catalog/ {
        limit_req zone=api_limit burst=30 nodelay;
        resolver 127.0.0.11 valid=10s;
        set $catalog_backend http://gpcg-catalog:8788;
        rewrite ^/gpcg/api/catalog/(.*)$ /api/$1 break;
        proxy_pass         $catalog_backend;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Connection        "";
        proxy_buffering    on;
        proxy_read_timeout 30s;
    }
"""

# Try to replace existing block (from "# ── GPCG" comment to the last "}")
# The block now contains multiple location blocks, so we match until the
# "# ── BI Identity" comment or the next non-GPCG section.
pattern = re.compile(
    r"    # ── GPCG \(Gameplay Content Generator\).*?(?=\n    # ── |\n    location /id/|\n    # ── Default|\Z)",
    re.DOTALL,
)
if pattern.search(content):
    content = pattern.sub(location_block.rstrip(), content, count=1)
elif "location /gpcg/" in content:
    # Fallback: replace from "location /gpcg/" — match all consecutive GPCG blocks
    pattern2 = re.compile(
        r"    (?:# ── GPCG.*?|location /gpcg/.*?|location ~ \^/gpcg/.*?)+    \}\n",
        re.DOTALL,
    )
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
ok "Nginx config atualizado"

# ── Step 5: Testar e recarregar nginx ─────────────────────────────────────────
log "Step 5/7: Testando e recarregando nginx..."
vps "docker exec trivestia-nginx nginx -t && docker exec trivestia-nginx nginx -s reload"
ok "Nginx recarregado"

# ── Step 6: Aguardar health check ────────────────────────────────────────────
log "Step 6/7: Aguardando health check..."
sleep 10
HEALTH=$(vps "docker inspect --format='{{.State.Health.Status}}' gpcg-api 2>/dev/null || echo 'starting'")
log "  Health status: $HEALTH"

# ── Step 7: Smoke test ───────────────────────────────────────────────────────
log "Step 7/7: Smoke test..."

log "  Verificando API pública..."
API_PUBLIC=$(curl -sf --max-time 10 https://brunointegrations.com/gpcg/api/health 2>&1 || echo "FAIL")
API_OK=0
if [[ "$API_PUBLIC" != "FAIL" ]]; then
  ok "API pública respondendo: $API_PUBLIC"
  API_OK=1
else
  err "API pública não respondeu"
fi

log "  Verificando Catalog Service..."
CATALOG_HEALTH=$(vps "docker inspect --format='{{.State.Health.Status}}' gpcg-catalog 2>/dev/null || echo 'starting'")
if [[ "$CATALOG_HEALTH" == "healthy" || "$CATALOG_HEALTH" == "starting" ]]; then
  ok "Catalog container: $CATALOG_HEALTH"
else
  err "Catalog container: $CATALOG_HEALTH"
fi

# ── Step 8: Versionamento + commit + tag + push ──────────────────────────────
if [[ "$NO_COMMIT" -eq 1 ]]; then
  log "Versionamento pulado (--no-commit)"
else
  if [[ "$API_OK" -eq 1 ]]; then
    NEW_VERSION=$(bump_version "$CURRENT_VERSION" "$BUMP")

    set_version "$NEW_VERSION"
    git add pyproject.toml
    git commit -m "chore: bump versão v$NEW_VERSION

Deploy: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Rollback: $DEPLOY_TAG"

    git tag "v$NEW_VERSION"
    ok "Versão bumpada: v$CURRENT_VERSION → v$NEW_VERSION"

    git push origin main 2>/dev/null && ok "Commits enviados" || err "Falha ao pushar commits"
    git push origin "v$NEW_VERSION" 2>/dev/null && ok "Tag v$NEW_VERSION enviada" || err "Falha ao pushar tag"
  else
    err "Smoke test falhou — versão não bumpada"
    err "Para rollback: git checkout $DEPLOY_TAG && ./deploy.sh --no-build"
  fi
fi

# ── Resumo final ─────────────────────────────────────────────────────────────
echo ""
log "═══════════════════════════════════════════════════════════════"
if [[ "$NO_COMMIT" -eq 0 && "${API_OK:-0}" -eq 1 ]]; then
  log "  Deploy concluído! — v${NEW_VERSION:-$CURRENT_VERSION}"
  log "  URL: https://brunointegrations.com/gpcg/"
  log "  API: https://brunointegrations.com/gpcg/api/health"
  log "  Tag: v${NEW_VERSION:-$CURRENT_VERSION}"
  log "  Rollback: $DEPLOY_TAG"
else
  log "  Deploy concluído (com warnings)"
  log "  URL: https://brunointegrations.com/gpcg/"
  log "  API: https://brunointegrations.com/gpcg/api/health"
fi
log "═══════════════════════════════════════════════════════════════"
