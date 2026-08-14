#!/bin/bash
# Deploy script for GPCG worker on Flávio's VM (CPU-only).
#
# This script installs and configures the GPCG worker on a fresh Ubuntu/Debian VM.
# It sets up:
#   - System dependencies (FFmpeg, Python, WireGuard)
#   - GPCG with CPU-only dependencies (no torch, no GPU packages)
#   - video-generate with requirements-cpu.txt (Kokoro TTS, no XTTS)
#   - Kokoro TTS model files
#   - systemd service for auto-start
#
# Prerequisites:
#   - Fresh Ubuntu 22.04+ or Debian 12+ VM
#   - Root access
#   - WireGuard tunnel to VPS already configured (setup-wireguard.sh)
#
# Usage:
#   sudo ./deploy-vm-worker.sh
#
# After deployment:
#   - Edit /opt/gpcg-worker/.env with API keys
#   - Start: systemctl --user start gpcg-worker-vm
#   - Logs:  journalctl --user -u gpcg-worker-vm -f

set -euo pipefail

# ─── Configuration ──────────────────────────────────────────────────────
GPCG_REPO="/home/bruno/Desenvolvimento/brunointegrations/gameplay-content-generator"
VG_REPO="/home/bruno/Desenvolvimento/brunointegrations/video-generate"
AI_MEDIA_CORE_REPO="/home/bruno/Desenvolvimento/brunointegrations/ai-media-core"

INSTALL_DIR="/opt/gpcg-worker"
VG_INSTALL_DIR="/opt/video-generate"
AI_MEDIA_CORE_INSTALL_DIR="/opt/ai-media-core"
VENV_DIR="$INSTALL_DIR/.venv"
VG_VENV_DIR="$VG_INSTALL_DIR/.venv"

# ─── Helper functions ───────────────────────────────────────────────────

log() {
    echo "[$(date +'%H:%M:%S')] $1"
}

error() {
    echo "❌ ERROR: $1" >&2
    exit 1
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        error "Please run as root (use sudo)"
    fi
}

# ─── Step 1: System dependencies ────────────────────────────────────────

install_system_deps() {
    log "📦 Installing system dependencies..."

    apt-get update -qq
    apt-get install -y -qq \
        python3 python3-venv python3-pip \
        ffmpeg \
        git \
        wireguard-tools \
        curl \
        build-essential \
        libsndfile1 \
        espeak-ng \
        2>&1 | tail -5

    log "✅ System dependencies installed"
}

# ─── Step 2: Clone/copy repositories ────────────────────────────────────

copy_repos() {
    log "📂 Copying repositories to /opt..."

    # GPCG
    if [ -d "$GPCG_REPO" ]; then
        mkdir -p "$INSTALL_DIR"
        rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
            "$GPCG_REPO/" "$INSTALL_DIR/"
        log "✅ GPCG copied to $INSTALL_DIR"
    else
        error "GPCG repo not found at $GPCG_REPO"
    fi

    # video-generate
    if [ -d "$VG_REPO" ]; then
        mkdir -p "$VG_INSTALL_DIR"
        rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
            --exclude='internal_media_library' \
            --exclude='gpcg_vg_*' \
            --exclude='data/jobs' \
            "$VG_REPO/" "$VG_INSTALL_DIR/"
        log "✅ video-generate copied to $VG_INSTALL_DIR"
    else
        error "video-generate repo not found at $VG_REPO"
    fi

    # ai-media-core
    if [ -d "$AI_MEDIA_CORE_REPO" ]; then
        mkdir -p "$AI_MEDIA_CORE_INSTALL_DIR"
        rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
            "$AI_MEDIA_CORE_REPO/" "$AI_MEDIA_CORE_INSTALL_DIR/"
        log "✅ ai-media-core copied to $AI_MEDIA_CORE_INSTALL_DIR"
    else
        error "ai-media-core repo not found at $AI_MEDIA_CORE_REPO"
    fi
}

# ─── Step 3: Python venvs ───────────────────────────────────────────────

create_venvs() {
    log "🐍 Creating Python virtual environments..."

    # GPCG venv
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -e "$INSTALL_DIR" -q 2>&1 | tail -3
    log "✅ GPCG venv created at $VENV_DIR"

    # video-generate venv (CPU-only)
    python3 -m venv "$VG_VENV_DIR"
    "$VG_VENV_DIR/bin/pip" install --upgrade pip -q
    "$VG_VENV_DIR/bin/pip" install -r "$VG_INSTALL_DIR/requirements-cpu.txt" -q 2>&1 | tail -5
    log "✅ video-generate venv created (CPU-only) at $VG_VENV_DIR"
}

# ─── Step 4: Kokoro TTS model ───────────────────────────────────────────

download_kokoro_model() {
    log "🔊 Downloading Kokoro TTS model files..."

    local model_dir="$VG_INSTALL_DIR/models"
    mkdir -p "$model_dir"

    # Download model files if not present
    if [ ! -f "$model_dir/kokoro-v1.0.onnx" ]; then
        log "   Downloading kokoro-v1.0.onnx (~80MB)..."
        curl -L -o "$model_dir/kokoro-v1.0.onnx" \
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx" \
            2>&1 | tail -3
    else
        log "   kokoro-v1.0.onnx already exists"
    fi

    if [ ! -f "$model_dir/voices-v1.0.bin" ]; then
        log "   Downloading voices-v1.0.bin (~10MB)..."
        curl -L -o "$model_dir/voices-v1.0.bin" \
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin" \
            2>&1 | tail -3
    else
        log "   voices-v1.0.bin already exists"
    fi

    log "✅ Kokoro TTS model files ready"
}

# ─── Step 5: BGM library ────────────────────────────────────────────────

copy_bgm_library() {
    log "🎵 Copying BGM library..."

    local bgm_src="$VG_REPO/internal_media_library/audios/bgm"
    local bgm_dst="$VG_INSTALL_DIR/internal_media_library/audios/bgm"

    if [ -d "$bgm_src" ]; then
        mkdir -p "$(dirname "$bgm_dst")"
        rsync -a "$bgm_src/" "$bgm_dst/"
        log "✅ BGM library copied ($(du -sh "$bgm_dst" | cut -f1))"
    else
        log "⚠️  BGM library not found at $bgm_src — video generation will fail without BGM"
        log "   Copy it manually: rsync -a $bgm_src/ $bgm_dst/"
    fi
}

# ─── Step 6: Storage directories ────────────────────────────────────────

create_storage_dirs() {
    log "📁 Creating storage directories..."

    mkdir -p "$INSTALL_DIR/data/gameplays"
    mkdir -p "$INSTALL_DIR/data/mapped"
    mkdir -p "$INSTALL_DIR/data/renders"
    mkdir -p "$INSTALL_DIR/data/outputs"
    mkdir -p "$INSTALL_DIR/data/knowledge"

    log "✅ Storage directories created at $INSTALL_DIR/data"
}

# ─── Step 7: Environment file ───────────────────────────────────────────

create_env_file() {
    log "📝 Creating .env file..."

    cat > "$INSTALL_DIR/.env" << 'EOF'
# GPCG Worker VM — Environment Configuration
# Edit these values before starting the worker!

# ── VPS connection (via WireGuard) ──────────────────────────────────────
GPCG_VPS_URL=http://10.10.0.1:8787/gpcg
GPCG_WORKER_ID=flavio-vm
GPCG_WORKER_API_KEY=CHANGE_ME

# ── Storage ─────────────────────────────────────────────────────────────
GPCG_WORKER_STORAGE=/opt/gpcg-worker/data
GPCG_WORKER_CAPABILITIES=mapping,generation

# ── Remote AI providers (LiteLLM proxy on VPS) ──────────────────────────
GPCG_LLM_PROVIDER=litellm
GPCG_LITELLM_BASE_URL=http://10.10.0.1:4000/v1
GPCG_LITELLM_API_KEY=CHANGE_ME_LITELLM
GPCG_LLM_MODEL_LITELLM=ollama/llama3.1:8b
GPCG_VLM_MODEL_LITELLM=ollama/gemma3:12b

# ── Remote ASR ──────────────────────────────────────────────────────────
GPCG_ASR_PROVIDER=litellm
GPCG_ASR_MODEL_LITELLM=whisper

# ── YOLO on CPU ─────────────────────────────────────────────────────────
GPCG_YOLO_DEVICE=cpu

# ── video-generate integration ──────────────────────────────────────────
VIDEO_GENERATE_DIR=/opt/video-generate
AI_MEDIA_CORE_DIR=/opt/ai-media-core/src
VIDEO_GENERATE_PYTHON=/opt/video-generate/.venv/bin/python

# ── Kokoro TTS (CPU-only) ───────────────────────────────────────────────
TTS_ENGINE=kokoro
KOKORO_MODEL_PATH=/opt/video-generate/models/kokoro-v1.0.onnx
KOKORO_VOICES_PATH=/opt/video-generate/models/voices-v1.0.bin
KOKORO_DEFAULT_VOICE=pm_alex

# ── Gameplay search dirs ────────────────────────────────────────────────
GPCG_GAMEPLAY_SEARCH_DIRS=/opt/gpcg-worker/data/gameplays
EOF

    chmod 600 "$INSTALL_DIR/.env"
    log "✅ .env file created at $INSTALL_DIR/.env"
    log "⚠️  EDIT $INSTALL_DIR/.env with real API keys before starting!"
}

# ─── Step 8: systemd service ────────────────────────────────────────────

install_systemd_service() {
    log "⚙️  Installing systemd service..."

    local service_src="$INSTALL_DIR/scripts/gpcg-worker-vm.service"
    local service_dst="/etc/systemd/system/gpcg-worker-vm.service"

    if [ -f "$service_src" ]; then
        cp "$service_src" "$service_dst"
        systemctl daemon-reload
        log "✅ systemd service installed (gpcg-worker-vm)"
        log "   Start with: systemctl start gpcg-worker-vm"
        log "   Enable with: systemctl enable gpcg-worker-vm"
    else
        log "⚠️  Service file not found at $service_src"
        log "   Install manually from scripts/gpcg-worker-vm.service"
    fi
}

# ─── Main ───────────────────────────────────────────────────────────────

main() {
    check_root

    echo "═══════════════════════════════════════════════════════════════"
    echo "  GPCG Worker VM Deployment (CPU-only, Flávio)"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""

    install_system_deps
    copy_repos
    create_venvs
    download_kokoro_model
    copy_bgm_library
    create_storage_dirs
    create_env_file
    install_systemd_service

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  ✅ Deployment complete!"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    echo "Next steps:"
    echo "  1. Edit $INSTALL_DIR/.env with real API keys"
    echo "  2. Ensure WireGuard tunnel is up: ping 10.10.0.1"
    echo "  3. Start the worker: systemctl start gpcg-worker-vm"
    echo "  4. Check logs: journalctl -u gpcg-worker-vm -f"
    echo ""
    echo "To verify the worker is running:"
    echo "  systemctl status gpcg-worker-vm"
    echo ""
}

main "$@"
