#!/usr/bin/env bash
# =============================================================================
# setup-vps.sh — Configure a VPS como Control Plane do GPCG
#
# Este script roda LOCALMENTE e configura a VPS via my-vps.
# Ele NÃO faz o deploy do código (use deploy.sh para isso) — apenas:
#   1. Gera uma worker API key
#   2. Adiciona ao .env da VPS
#   3. Salva a key localmente para o worker usar
#   4. Verifica se o container está rodando
#
# Uso:
#   ./scripts/setup-vps.sh              # configuração inicial
#   ./scripts/setup-vps.sh --check      # apenas verifica estado atual
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
info() { echo -e "${CYAN}ℹ${NC} $1"; }
step() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

VPS_DIR="/opt/gpcg"
KEY_FILE="$HOME/.config/gpcg-worker-key.env"

check_vps() {
    step "Verificando VPS"

    # my-vps
    if ! command -v my-vps &>/dev/null; then
        fail "my-vps não encontrado — instale o CLI da trivestia"
        return 1
    fi
    ok "my-vps disponível"

    # Conectividade
    if my-vps "echo ok" &>/dev/null; then
        ok "Conexão SSH com VPS funcionando"
    else
        fail "Não foi possível conectar à VPS via my-vps"
        return 1
    fi

    # Docker
    if my-vps "docker --version" &>/dev/null; then
        ok "Docker instalado na VPS"
    else
        fail "Docker não encontrado na VPS"
        return 1
    fi

    # Diretório do GPCG
    if my-vps "test -d $VPS_DIR" &>/dev/null; then
        ok "Diretório $VPS_DIR existe"
    else
        warn "Diretório $VPS_DIR não existe — execute deploy.sh primeiro"
        return 1
    fi

    # .env
    if my-vps "test -f $VPS_DIR/.env" &>/dev/null; then
        ok "Arquivo .env existe"
    else
        fail "Arquivo .env não encontrado em $VPS_DIR/.env"
        return 1
    fi

    # Container rodando
    local container_status
    container_status=$(my-vps "docker ps --filter name=gpcg-api --format '{{.Status}}'" 2>/dev/null || echo "")
    if echo "$container_status" | grep -q "Up"; then
        ok "Container gpcg-api rodando ($container_status)"
    else
        warn "Container gpcg-api não está rodando — execute deploy.sh"
    fi

    # API respondendo
    local health
    health=$(my-vps "curl -s http://gpcg-api:8787/api/health" 2>/dev/null || echo "FAIL")
    if echo "$health" | grep -q '"status":"ok"'; then
        ok "API respondendo: $health"
    else
        warn "API não respondeu ainda (pode estar iniciando)"
    fi

    # Worker API key já configurada?
    local has_key
    has_key=$(my-vps "grep -c GPCG_WORKER_API_KEY $VPS_DIR/.env" 2>/dev/null || echo "0")
    if [ "$has_key" -gt 0 ]; then
        ok "Worker API key já configurada no .env da VPS"
    else
        warn "Worker API key NÃO configurada — execute setup-vps.sh (sem --check)"
    fi
}

generate_key() {
    step "Gerando worker API key"

    # Verifica se já existe
    local existing_key
    existing_key=$(my-vps "grep GPCG_WORKER_API_KEY $VPS_DIR/.env 2>/dev/null | cut -d= -f2" 2>/dev/null || echo "")

    if [ -n "$existing_key" ]; then
        info "Worker API key já existe no .env da VPS"
        read -p "Gerar nova key e substituir? (y/N) " replace
        [ "$replace" != "y" ] && {
            # Usa a key existente
            local key="$existing_key"
            save_key_locally "$key"
            return
        }
        # Remove a linha antiga
        my-vps "sed -i '/GPCG_WORKER_API_KEY/d' $VPS_DIR/.env"
        my-vps "sed -i '/GPCG_WORKER_HEARTBEAT_TIMEOUT/d' $VPS_DIR/.env"
        my-vps "sed -i '/GPCG_WORKER_POLL_INTERVAL/d' $VPS_DIR/.env"
        my-vps "sed -i '/GPCG_WORKER_CONCURRENCY/d' $VPS_DIR/.env"
        my-vps "sed -i '/Worker API (Control Plane/d' $VPS_DIR/.env"
    fi

    # Gera nova key
    local key
    key=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    ok "Nova key gerada: ${key:0:16}..."

    # Adiciona ao .env da VPS
    my-vps "echo '
# ── Worker API (Control Plane ↔ Compute Plane) ───────────────────────────────
GPCG_WORKER_API_KEY=$key
GPCG_WORKER_HEARTBEAT_TIMEOUT=30
GPCG_WORKER_POLL_INTERVAL=5
GPCG_WORKER_CONCURRENCY=1' >> $VPS_DIR/.env"
    ok "Key adicionada ao .env da VPS"

    # Reinicia container para aplicar
    info "Reiniciando container para aplicar a nova key..."
    my-vps "cd $VPS_DIR && docker compose -f docker-compose.prod.yml restart gpcg-api"
    sleep 5

    # Verifica
    local health
    health=$(my-vps "curl -s http://gpcg-api:8787/api/health" 2>/dev/null || echo "FAIL")
    if echo "$health" | grep -q '"status":"ok"'; then
        ok "Container reiniciado e API respondendo"
    else
        warn "Container pode ainda estar iniciando — aguarde 10s"
    fi

    save_key_locally "$key"
}

save_key_locally() {
    local key="$1"
    step "Salvando key localmente"

    mkdir -p "$(dirname "$KEY_FILE")"
    cat > "$KEY_FILE" << EOF
# GPCG Worker API key (gerado por setup-vps.sh)
# Compartilhe esta key com o worker local (setup-worker.sh lê este arquivo)
GPCG_WORKER_API_KEY=$key
EOF
    chmod 600 "$KEY_FILE"
    ok "Key salva em $KEY_FILE"
    info "O script setup-worker.sh vai ler este arquivo automaticamente"
}

# ── Menu principal ───────────────────────────────────────────────────────────

main() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║   GPCG VPS Setup — Control Plane                            ║"
    echo "║   Configura a VPS para receber workers remotos              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    if [ "${1:-}" = "--check" ]; then
        check_vps
        exit $?
    fi

    # Verifica estado atual
    check_vps || true

    # Gera/configura key
    generate_key

    step "Configuração da VPS concluída!"
    echo ""
    ok "Próximos passos:"
    echo ""
    echo "  1. Faça deploy do código (se não fez ainda):"
    echo -e "     ${CYAN}./scripts/deploy.sh${NC}"
    echo ""
    echo "  2. Configure o worker no PC local com GPU:"
    echo -e "     ${CYAN}./scripts/setup-worker.sh${NC}"
    echo ""
    echo "  3. Inicie o worker:"
    echo -e "     ${CYAN}systemctl --user enable --now gpcg-worker${NC}"
    echo ""
    echo "  4. Acesse o dashboard:"
    echo "     https://brunointegrations.com/gpcg/dashboard"
}

main "$@"
