#!/usr/bin/env bash
# =============================================================================
# setup-worker.sh — Configure e instale o GPCG Remote Worker (Compute Plane)
#
# Este script roda no PC LOCAL (com GPU) e configura tudo para o worker
# conectar à VPS e processar jobs de mapeamento e geração de vídeos.
#
# Uso:
#   ./scripts/setup-worker.sh              # instalação interativa
#   ./scripts/setup-worker.sh --check     # apenas verifica pré-requisitos
#
# Pré-requisitos (verificados pelo script):
#   - Python 3.11+
#   - NVIDIA GPU + nvidia-smi funcionando
#   - Ollama rodando (com gemma3:12b e gpt-oss:latest instalados)
#   - video-generate instalado em ../video-generate
#   - HD de gravações montado (ex: /media/bruno/ToshibaHD)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
info() { echo -e "${CYAN}ℹ${NC} $1"; }
step() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

# ── Pré-requisitos ───────────────────────────────────────────────────────────

check_prerequisites() {
    step "Verificando pré-requisitos"
    local errors=0

    # Python
    if command -v python3 &>/dev/null; then
        local py_ver
        py_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        local py_minor
        py_minor=$(python3 -c 'import sys; print(sys.version_info.minor)')
        if [ "$py_minor" -ge 11 ]; then
            ok "Python $py_ver"
        else
            fail "Python 3.11+ necessário (encontrado $py_ver)"
            errors=$((errors + 1))
        fi
    else
        fail "Python 3 não encontrado"
        errors=$((errors + 1))
    fi

    # NVIDIA GPU
    if command -v nvidia-smi &>/dev/null; then
        local gpu_name
        gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits 2>/dev/null | head -1)
        if [ -n "$gpu_name" ]; then
            ok "GPU: $gpu_name"
        else
            fail "nvidia-smi não retornou GPU"
            errors=$((errors + 1))
        fi
    else
        fail "nvidia-smi não encontrado — NVIDIA driver necessário"
        errors=$((errors + 1))
    fi

    # Ollama
    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        ok "Ollama rodando em localhost:11434"
        # Verifica modelos
        local models
        models=$(curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    names = [m['name'] for m in data.get('models', [])]
    print(' '.join(names))
except: print('')
" 2>/dev/null)

        if echo "$models" | grep -q "gemma3:12b"; then
            ok "Modelo gemma3:12b instalado (VLM para análise de gameplay)"
        else
            warn "Modelo gemma3:12b NÃO instalado — instale com: ollama pull gemma3:12b"
        fi

        if echo "$models" | grep -q "qwen3:14b"; then
            ok "Modelo qwen3:14b instalado (creative engine)"
        else
            warn "Modelo qwen3:14b NÃO instalado — instale com: ollama pull qwen3:14b"
        fi

        if echo "$models" | grep -q "llama3.1:8b"; then
            ok "Modelo llama3.1:8b instalado (LLM para roteiro/metadata)"
        else
            warn "Modelo llama3.1:8b NÃO instalado — instale com: ollama pull llama3.1:8b"
        fi
    else
        fail "Ollama não está rodando — inicie com: ollama serve"
        errors=$((errors + 1))
    fi

    # video-generate
    local vg_dir="${VIDEO_GENERATE_DIR:-$HOME/Desenvolvimento/brunointegrations/video-generate}"
    if [ -d "$vg_dir" ] && [ -f "$vg_dir/.venv/bin/python" ]; then
        ok "video-generate encontrado em $vg_dir"
    else
        fail "video-generate não encontrado em $vg_dir"
        warn "  Clone: git clone <repo> $vg_dir && cd $vg_dir && ./scripts/setup.sh"
        errors=$((errors + 1))
    fi

    # ai-media-core
    local amc_dir="${AI_MEDIA_CORE_DIR:-$HOME/Desenvolvimento/brunointegrations/ai-media-core}"
    if [ -d "$amc_dir/src" ]; then
        ok "ai-media-core encontrado em $amc_dir"
    else
        fail "ai-media-core não encontrado em $amc_dir"
        errors=$((errors + 1))
    fi

    # HD de gravações
    local hd_dir="${GPCG_WORKER_STORAGE:-/media/bruno/ToshibaHD}"
    if [ -d "$hd_dir" ]; then
        ok "HD de gravações montado em $hd_dir"
    else
        warn "HD não montado em $hd_dir — o worker criará o diretório"
    fi

    echo ""
    if [ $errors -gt 0 ]; then
        fail "$errors pré-requisito(s) não atendido(s)"
        return 1
    else
        ok "Todos os pré-requisitos atendidos!"
        return 0
    fi
}

# ── Instalação ───────────────────────────────────────────────────────────────

install_venv() {
    step "Configurando Python venv"

    if [ -d "$VENV" ]; then
        info "venv já existe — atualizando dependências..."
    else
        info "Criando venv..."
        python3 -m venv "$VENV"
    fi

    "$VENV/bin/pip" install --upgrade pip wheel
    info "Instalando dependências base..."
    "$VENV/bin/pip" install -e ".[dev]"

    info "Instalando dependências GPU (torch, ultralytics, whisper)..."
    "$VENV/bin/pip" install -e ".[gpu]"

    ok "venv configurado com dependências GPU"
}

install_config() {
    step "Configurando credenciais"

    # Pergunta URL da VPS
    local vps_url
    vps_url=$(cat ~/.config/gpcg-worker-key.env 2>/dev/null | grep GPCG_VPS_URL | cut -d= -f2 || echo "")
    if [ -z "$vps_url" ]; then
        read -p "URL da VPS [https://brunointegrations.com/gpcg]: " vps_url
        vps_url=${vps_url:-https://brunointegrations.com/gpcg}
    fi

    # Pergunta worker ID
    local worker_id
    read -p "ID do worker (nome único) [home-pc]: " worker_id
    worker_id=${worker_id:-home-pc}

    # Pergunta API key
    local api_key
    api_key=$(cat ~/.config/gpcg-worker-key.env 2>/dev/null | grep GPCG_WORKER_API_KEY | cut -d= -f2 || echo "")
    if [ -z "$api_key" ]; then
        read -p "Worker API key (do .env da VPS): " api_key
    else
        info "API key encontrada em ~/.config/gpcg-worker-key.env"
    fi

    if [ -z "$api_key" ]; then
        fail "API key é obrigatória — configure GPCG_WORKER_API_KEY no .env da VPS primeiro"
        return 1
    fi

    # Pergunta HD
    local storage_dir
    read -p "Diretório de armazenamento local [/media/bruno/ToshibaHD/gpcg]: " storage_dir
    storage_dir=${storage_dir:-/media/bruno/ToshibaHD/gpcg}

    # Salva credenciais
    mkdir -p ~/.config
    cat > ~/.config/gpcg-worker-key.env << EOF
# GPCG Remote Worker — credenciais (gerado por setup-worker.sh)
GPCG_VPS_URL=$vps_url
GPCG_WORKER_ID=$worker_id
GPCG_WORKER_API_KEY=$api_key
GPCG_WORKER_STORAGE=$storage_dir
GPCG_WORKER_CAPABILITIES=mapping,generation
EOF
    chmod 600 ~/.config/gpcg-worker-key.env
    ok "Credenciais salvas em ~/.config/gpcg-worker-key.env"

    # Cria diretórios de armazenamento
    mkdir -p "$storage_dir"/{gameplays,mapped,renders,outputs}
    ok "Diretórios criados em $storage_dir"
}

install_systemd() {
    step "Configurando serviço systemd"

    local service_dir="$HOME/.config/systemd/user"
    mkdir -p "$service_dir"

    # Gera o service file com as credenciais
    local vg_dir="${VIDEO_GENERATE_DIR:-$HOME/Desenvolvimento/brunointegrations/video-generate}"
    local amc_dir="${AI_MEDIA_CORE_DIR:-$HOME/Desenvolvimento/brunointegrations/ai-media-core}"
    source ~/.config/gpcg-worker-key.env

    cat > "$service_dir/gpcg-worker.service" << EOF
[Unit]
Description=GPCG Remote Worker (Compute Plane)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$VENV/bin/gpcg remote-worker

# Credenciais (de ~/.config/gpcg-worker-key.env)
Environment=GPCG_VPS_URL=$GPCG_VPS_URL
Environment=GPCG_WORKER_ID=$GPCG_WORKER_ID
Environment=GPCG_WORKER_API_KEY=$GPCG_WORKER_API_KEY
Environment=GPCG_WORKER_STORAGE=$GPCG_WORKER_STORAGE
Environment=GPCG_WORKER_CAPABILITIES=$GPCG_WORKER_CAPABILITIES

# AI services
Environment=OLLAMA_HOST=http://localhost:11434
Environment=GPCG_GAMEPLAY_VISION_MODEL=gemma3:12b
Environment=GPCG_GAMEPLAY_ASR_DEVICE=cuda

# video-generate integration
Environment=VIDEO_GENERATE_DIR=$vg_dir
Environment=AI_MEDIA_CORE_DIR=$amc_dir/src
Environment=VIDEO_GENERATE_PYTHON=$vg_dir/.venv/bin/python

Restart=on-failure
RestartSec=10
StartLimitInterval=300
StartLimitBurst=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=gpcg-worker

[Install]
WantedBy=default.target
EOF

    ok "Service file criado em $service_dir/gpcg-worker.service"

    systemctl --user daemon-reload
    ok "systemd recarregado"

    echo ""
    info "Para iniciar o worker:"
    echo "  systemctl --user enable --now gpcg-worker"
    echo ""
    info "Para ver logs:"
    echo "  journalctl --user -u gpcg-worker -f"
    echo ""
    info "Para parar:"
    echo "  systemctl --user stop gpcg-worker"
}

test_connection() {
    step "Testando conexão com a VPS"

    source ~/.config/gpcg-worker-key.env

    info "Testando health check..."
    local health
    health=$(curl -s "${GPCG_VPS_URL}/api/health" 2>/dev/null || echo "FAIL")
    if echo "$health" | grep -q '"status":"ok"'; then
        ok "VPS respondeu: $health"
    else
        fail "VPS não respondeu em ${GPCG_VPS_URL}/api/health"
        warn "  Verifique a URL e se a VPS está online"
        return 1
    fi

    info "Testando autenticação do worker..."
    local reg
    reg=$(curl -s -X POST "${GPCG_VPS_URL}/api/workers/register" \
        -H "Content-Type: application/json" \
        -H "X-Worker-Key: $GPCG_WORKER_API_KEY" \
        -d "{\"worker_id\":\"${GPCG_WORKER_ID}-setup-test\",\"hostname\":\"$(hostname)\",\"capabilities\":[\"mapping\"],\"worker_version\":\"0.1.0\"}" 2>/dev/null || echo "FAIL")

    if echo "$reg" | grep -q '"registered":true'; then
        ok "Worker autenticado com sucesso!"

        # Limpa worker de teste
        curl -s -X POST "${GPCG_VPS_URL}/api/workers/${GPCG_WORKER_ID}-setup-test/status" \
            -H "Content-Type: application/json" \
            -H "X-Worker-Key: $GPCG_WORKER_API_KEY" \
            -d '{"status":"offline","current_activity":"Setup test cleanup"}' >/dev/null 2>&1
        info "Worker de teste removido"
    elif echo "$reg" | grep -q "Invalid worker key"; then
        fail "API key inválida — verifique GPCG_WORKER_API_KEY"
        return 1
    else
        fail "Resposta inesperada: $reg"
        return 1
    fi
}

# ── Menu principal ───────────────────────────────────────────────────────────

main() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║   GPCG Remote Worker — Instalação (Compute Plane)           ║"
    echo "║   Processa gameplays e gera vídeos usando GPU local         ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    if [ "${1:-}" = "--check" ]; then
        check_prerequisites
        exit $?
    fi

    # Step 1: Verificar pré-requisitos
    if ! check_prerequisites; then
        echo ""
        warn "Alguns pré-requisitos não foram atendidos."
        read -p "Continuar mesmo assim? (y/N) " cont
        [ "$cont" != "y" ] && exit 1
    fi

    # Step 2: Instalar venv + deps
    install_venv

    # Step 3: Configurar credenciais
    install_config

    # Step 4: Testar conexão com VPS
    test_connection

    # Step 5: Configurar systemd
    install_systemd

    # Resumo final
    step "Instalação concluída!"
    echo ""
    ok "Tudo configurado. Para iniciar o worker:"
    echo ""
    echo -e "  ${CYAN}systemctl --user enable --now gpcg-worker${NC}"
    echo ""
    echo "  Logs:    journalctl --user -u gpcg-worker -f"
    echo "  Status:  systemctl --user status gpcg-worker"
    echo "  Parar:   systemctl --user stop gpcg-worker"
    echo ""
    echo "  Dashboard: https://brunointegrations.com/gpcg/dashboard"
    echo ""
    info "O worker vai aparecer no dashboard assim que iniciar."
}

main "$@"
