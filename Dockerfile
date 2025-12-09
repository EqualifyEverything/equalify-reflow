# Equalify PDF Converter

# ==============================================================================
# Stage 1: Frontend - Build React demo UI
# ==============================================================================
FROM node:20-alpine AS frontend-builder

# Enable pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

WORKDIR /app

# Install dependencies first (better caching)
COPY frontend/demo-ui/package.json frontend/demo-ui/pnpm-lock.yaml* frontend/demo-ui/.npmrc ./
RUN pnpm install --frozen-lockfile

# Copy source and build
COPY frontend/demo-ui/ ./
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
# Stage 4: Development - Hot-reload for fast iteration
# ==============================================================================
FROM dependencies AS development

# Install development dependencies (for testing)
RUN uv sync --frozen --all-extras || uv sync --all-extras

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
COPY --from=frontend-builder /app/dist ./static/demo-ui

# Health check for orchestration (ECS, Kubernetes, Docker Compose)
# Checks /health endpoint every 30 seconds
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health').read()" || exit 1

# Expose API port
EXPOSE 8080

# Production command (no reload for stability)
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
