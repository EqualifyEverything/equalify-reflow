# Equalify Reflow

# ==============================================================================
# Stage 1: Frontend - Build Pipeline Viewer
# ==============================================================================
FROM node:20-alpine AS frontend-builder

# Enable pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

WORKDIR /app

# Install dependencies first (better caching)
COPY clients/viewer/package.json clients/viewer/pnpm-lock.yaml* clients/viewer/.npmrc ./
RUN pnpm install --frozen-lockfile

# Copy source and build
COPY clients/viewer/ ./
RUN pnpm run build

# ==============================================================================
# Stage 2: Base - Common foundation for Python stages
# ==============================================================================
FROM python:3.11-slim AS base

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Set working directory
WORKDIR /app

# ==============================================================================
# Stage 3: Dependencies - Install Python dependencies
# ==============================================================================
FROM base AS dependencies

# Install minimal system dependencies (Presidio PII detection, poppler for PDF utils)
# NOTE: Tesseract and Docling model downloads are no longer needed — OCR and document
# conversion are handled by the docling-serve sidecar container.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    ghostscript \
    qpdf \
    pngquant \
    unpaper \
    liblouis-data \
    liblouis20 \
    liblouis-bin \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Sync dependencies (no more PyTorch or CUDA — Docling runs in sidecar)
RUN (uv sync --frozen || uv sync) \
    && rm -rf /root/.cache/uv

# Pre-download spaCy model for Presidio PII detection
# This avoids cold start delays when the PII worker processes its first request
# Note: Installing directly from GitHub releases instead of `python -m spacy download`
# because the latter requires pip, which uv doesn't install in virtualenvs by default
# Model version should match spacy version - check https://github.com/explosion/spacy-models/releases
RUN uv pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl

# ==============================================================================
# Stage 4: Development - Hot-reload for fast iteration
# ==============================================================================
FROM dependencies AS development

# Install dev dependencies
RUN (uv sync --frozen --all-extras || uv sync --all-extras) \
    && rm -rf /root/.cache/uv

# Copy source code and configuration
# Note: In dev, src will be overridden by volume mount in docker-compose.dev.yml
COPY src/ ./src/
COPY config/ ./config/

# Expose API port
EXPOSE 8080

# Development command with hot-reload
CMD ["uv", "run", "uvicorn", "src.main:app", "--reload", "--host", "0.0.0.0", "--port", "8080"]

# ==============================================================================
# Stage 5: Production - Optimized for deployment
# ==============================================================================
FROM dependencies AS production

# Copy source code and configuration
COPY src/ ./src/
COPY config/ ./config/

# Copy built frontend from frontend-builder stage
COPY --from=frontend-builder /app/dist ./static/viewer

# Health check for orchestration (ECS, Kubernetes, Docker Compose)
# Checks /health endpoint every 30 seconds
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health').read()" || exit 1

# Expose API port
EXPOSE 8080

# Production command (no reload for stability).
#
# --proxy-headers + --forwarded-allow-ips=* tells uvicorn to honour the
# X-Forwarded-Proto / X-Forwarded-For headers set by an upstream reverse
# proxy (ALB, Nginx, Cloudflare). Without this, request.url.scheme inside
# the container reads "http" even when the user hit "https", so any
# scheme-aware code path silently builds wrong URLs — most visibly the
# OIDC redirect_uri, which then mismatches what's registered with the IdP
# and login fails with AADSTS50011 (or the equivalent on Google/Okta/...).
# The "*" allowlist is safe behind ECS+ALB because the security group
# restricts ingress on :8080 to the ALB only, and the ALB strips any
# client-supplied X-Forwarded-* before forwarding.
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
