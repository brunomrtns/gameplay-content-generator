# =============================================================================
# Dockerfile — GPCG (Gameplay Content Generator) Multi-User Platform
#
# Multi-stage build:
#   1. Build frontend (Node 22 + Vite)
#   2. Python 3.12-slim runtime with FastAPI serving API + static frontend
#
# The container serves both the API (/api/*) and the built frontend (/*)
# on a single port (8787). nginx (trivestia-nginx) reverse-proxies via
# the /gpcg/ path prefix.
# =============================================================================

# ── Stage 1: Build frontend ───────────────────────────────────────────────────
FROM node:22-slim AS frontend-builder

WORKDIR /app/frontend

# Install dependencies
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

# Build
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Copy source first (editable install needs the package directory)
COPY pyproject.toml ./
COPY README.md ./
COPY src/ ./src/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir -e "."

# Copy built frontend
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 8787

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:8787/api/health').raise_for_status()" || exit 1

# Run
CMD ["python", "-m", "uvicorn", "gpcg.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8787"]
