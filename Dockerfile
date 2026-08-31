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

# Install system dependencies (cached unless apt packages change)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies — copy only pyproject.toml first so this layer
# is cached and only rebuilt when dependencies change (not on every code change)
COPY pyproject.toml ./
COPY README.md ./
# Create a minimal src/gpcg/__init__.py so editable install works without
# copying all source files (which would invalidate the cache)
RUN mkdir -p src/gpcg && echo "" > src/gpcg/__init__.py && \
    pip install --no-cache-dir -e "." && \
    rm -rf src/gpcg/__init__.py

# Now copy the actual source code (this layer changes on every code edit,
# but the pip install layer above is cached)
COPY src/ ./src/
COPY scripts/ ./scripts/

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
