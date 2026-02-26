# Equalify PDF Converter

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

# Install system dependencies for Docling (PDF processing) and Presidio (PII detection)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-spa \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-chi-sim \
    tesseract-ocr-jpn \
    tesseract-ocr-kor \
    tesseract-ocr-ara \
    tesseract-ocr-hin \
    tesseract-ocr-por \
    tesseract-ocr-ita \
    tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Sync dependencies with uv
# --frozen: Use exact versions from lock file
# Fallback to regular sync if no lock file exists
RUN uv sync --frozen || uv sync

# Replace CUDA PyTorch with CPU-only version (saves ~4GB of CUDA dependencies)
# GPU not supported on ECS Fargate, and document processing doesn't benefit from GPU
# Must run AFTER uv sync to overwrite the CUDA version in the venv
RUN uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Clean up orphaned CUDA packages pulled in by PyPI torch
RUN uv pip uninstall nvidia-cublas-cu12 nvidia-cuda-cupti-cu12 nvidia-cuda-nvrtc-cu12 \
    nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 nvidia-cufft-cu12 nvidia-cufile-cu12 \
    nvidia-curand-cu12 nvidia-cusolver-cu12 nvidia-cusparse-cu12 nvidia-cusparselt-cu12 \
    nvidia-nccl-cu12 nvidia-nvjitlink-cu12 nvidia-nvtx-cu12 triton 2>/dev/null || true

# Pre-download spaCy model for Presidio PII detection
# This avoids cold start delays when the PII worker processes its first request
# Note: Installing directly from GitHub releases instead of `python -m spacy download`
# because the latter requires pip, which uv doesn't install in virtualenvs by default
# Model version should match spacy version - check https://github.com/explosion/spacy-models/releases
RUN uv pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl

# Pre-download Docling models for PDF processing
# This avoids runtime model downloads that can timeout (504 errors) and adds 2-5 minutes to cold starts
# Models are cached to ~/.cache/docling/models (or DOCLING_ARTIFACTS_PATH)
# Download only essential models (skip easyocr which often fails to download; we use tesseract instead)
RUN uv run docling-tools models download layout tableformer code_formula picture_classifier --quiet

# ==============================================================================
# Stage 4: Development - Hot-reload for fast iteration
# ==============================================================================
FROM dependencies AS development

# Install development dependencies (for testing)
RUN uv sync --frozen --all-extras || uv sync --all-extras

# Re-apply CPU-only PyTorch (uv sync --all-extras may reinstall CUDA version)
RUN uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

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

# Production command (no reload for stability)
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
