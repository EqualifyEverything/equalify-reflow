# Equalify PDF Converter

# ==============================================================================
# Stage 1: Base - Common foundation for all stages
# ==============================================================================
FROM python:3.11-slim AS base

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Set working directory
WORKDIR /app

# ==============================================================================
# Stage 2: Dependencies - Install Python dependencies
# ==============================================================================
FROM base AS dependencies

# Install system dependencies for Docling (PDF processing) and Presidio (PII detection)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install CPU-only PyTorch first (avoids 4GB of CUDA dependencies)
# GPU not supported on ECS Fargate, and document processing doesn't benefit from GPU
RUN uv pip install --system torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Sync dependencies with uv (will skip torch/torchvision since already installed)
# --frozen: Use exact versions from lock file
# Fallback to regular sync if no lock file exists
RUN uv sync --frozen || uv sync

# ==============================================================================
# Stage 3: Development - Hot-reload for fast iteration
# ==============================================================================
FROM dependencies AS development

# Install development dependencies (for testing)
RUN uv sync --frozen --all-extras || uv sync --all-extras

# Copy source code
# Note: In dev, this will be overridden by volume mount in docker-compose.dev.yml
COPY src/ ./src/

# Expose API port
EXPOSE 8080

# Development command with hot-reload
CMD ["uv", "run", "uvicorn", "src.main:app", "--reload", "--host", "0.0.0.0", "--port", "8080"]

# ==============================================================================
# Stage 4: Production - Optimized for deployment
# ==============================================================================
FROM dependencies AS production

# Copy source code
COPY src/ ./src/

# Health check for orchestration (ECS, Kubernetes, Docker Compose)
# Checks /health endpoint every 30 seconds
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health').read()" || exit 1

# Expose API port
EXPOSE 8080

# Production command (no reload for stability)
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
