.PHONY: help dev dev-docker up down logs health test test-fast test-unit test-integration test-concurrent test-e2e test-large-files test-slow test-all clean build build-viewer build-viewer-dev shell test-docker logs-api grafana-url prometheus-url metrics-url coverage coverage-html coverage-report localstack-debug docling-native docling-native-stop docling-install

# Default target
help:
	@echo "Equalify Reflow - Makefile Commands"
	@echo ""
	@echo "Essential:"
	@echo "  make dev          - Start development (auto-detects native GPU docling)"
	@echo "  make dev-docker   - Start development (force Docker docling, CPU only)"
	@echo "  make down         - Stop all services"
	@echo "  make logs         - View all service logs"
	@echo "  make logs-api     - View API logs only"
	@echo "  make health       - Run health checks"
	@echo "  make test         - Run tests locally"
	@echo ""
	@echo "Testing & Coverage:"
	@echo "  make test         - Run all tests (excludes concurrent & large file tests)"
	@echo "  make test-fast    - Run fast unit tests in Docker (<30s with parallelization)"
	@echo "  make test-unit    - Run unit tests only (same as test-fast)"
	@echo "  make test-integration - Run integration tests (real Redis/S3, ~2min)"
	@echo "  make test-concurrent - Run concurrent integration tests (single-threaded, ~1min)"
	@echo "  make test-e2e     - Run E2E tests (full workflows, ~5min, excludes large files)"
	@echo "  make test-large-files - Run large file edge case tests (single-threaded, ~2min)"
	@echo "  make test-slow    - Run slow/E2E tests (same as test-e2e)"
	@echo "  make test-all     - Run all tests (unit in Docker, integration on host)"
	@echo "  make coverage     - Run tests with coverage report"
	@echo "  make coverage-html - Generate and open HTML coverage report"
	@echo "  make coverage-report - Show coverage summary"
	@echo ""
	@echo "Docker:"
	@echo "  make build        - Build Docker images"
	@echo "  make build-viewer - Build pipeline viewer (for dev, then restart)"
	@echo "  make shell        - Access API container shell"
	@echo "  make test-docker  - Run tests inside container"
	@echo ""
	@echo "Canvas LMS:"
	@echo "  make canvas       - Start local Canvas LMS"
	@echo "  make canvas-down  - Stop local Canvas LMS"
	@echo "  make canvas-logs  - View Canvas web logs"
	@echo ""
	@echo "Native Docling (GPU):"
	@echo "  make docling-install  - Install docling-serve natively (one-time)"
	@echo "  make docling-native   - Start native docling-serve (MPS/GPU)"
	@echo "  make docling-native-stop - Stop native docling-serve"
	@echo ""
	@echo "Utilities:"
	@echo "  make redis-cli    - Connect to Redis CLI"
	@echo "  make clean        - Remove containers and volumes"
	@echo ""
	@echo "Observability:"
	@echo "  make grafana-url  - Open Grafana (http://localhost:3001)"
	@echo "  make prometheus-url - Open Prometheus (http://localhost:9090)"
	@echo "  make metrics-url  - Open API metrics (http://localhost:8080/metrics)"
	@echo ""
	@echo "Debugging:"
	@echo "  make localstack-debug - Debug LocalStack from host (rarely needed)"
	@echo ""

# Development environment
# Auto-detects native docling-serve for GPU acceleration (Apple Silicon MPS).
# If installed, launches it automatically. Falls back to Docker CPU mode.
# Force Docker mode: make dev-docker
dev: build-viewer-dev
	@COMPOSE_FILES="-f docker-compose.yml -f docker-compose.dev.yml"; \
	USE_NATIVE=false; \
	if curl -sf http://localhost:5001/health > /dev/null 2>&1; then \
		echo "✅ Native docling-serve already running (GPU/MPS)"; \
		USE_NATIVE=true; \
	elif command -v docling-serve > /dev/null 2>&1; then \
		echo "Starting native docling-serve (GPU/MPS)..."; \
		DOCLING_DEVICE=mps \
		DOCLING_SERVE_LOAD_MODELS_AT_BOOT=true \
		DOCLING_SERVE_ENG_LOC_SHARE_MODELS=true \
		DOCLING_SERVE_MAX_SYNC_WAIT=600 \
		nohup docling-serve run > /tmp/docling-serve.log 2>&1 & echo $$! > /tmp/docling-serve.pid; \
		echo "   Waiting for docling-serve to start (logs: /tmp/docling-serve.log)..."; \
		for i in $$(seq 1 60); do \
			if curl -sf http://localhost:5001/health > /dev/null 2>&1; then \
				echo "✅ Native docling-serve ready (GPU/MPS)"; \
				USE_NATIVE=true; \
				break; \
			fi; \
			sleep 5; \
		done; \
		if [ "$$USE_NATIVE" = "false" ]; then \
			echo "⚠️  Native docling-serve failed to start — falling back to Docker (CPU)"; \
			echo "   Check /tmp/docling-serve.log for details"; \
		fi; \
	else \
		echo "ℹ️  Using Docker docling-serve (CPU mode)"; \
		echo "   For GPU acceleration: make docling-install (one-time setup)"; \
	fi; \
	if [ "$$USE_NATIVE" = "true" ]; then \
		COMPOSE_FILES="$$COMPOSE_FILES -f docker-compose.native-docling.yml"; \
	fi; \
	AWS_PROFILE=$${AWS_PROFILE:-uic}; \
	if aws sts get-caller-identity --profile $$AWS_PROFILE > /dev/null 2>&1; then \
		echo "✅ AWS credentials valid for profile $$AWS_PROFILE"; \
		eval "$$(aws configure export-credentials --profile $$AWS_PROFILE --format env)" && \
		export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN && \
		docker compose $$COMPOSE_FILES up -d; \
	else \
		echo "⚠️  AWS credentials not found for profile $$AWS_PROFILE - Bedrock AI unavailable"; \
		echo "   To enable: aws sso login --profile $$AWS_PROFILE && make down && make dev"; \
		docker compose $$COMPOSE_FILES up -d; \
	fi

# Development with Docker docling-serve only (skip native GPU detection)
dev-docker: build-viewer-dev
	@AWS_PROFILE=$${AWS_PROFILE:-uic}; \
	echo "ℹ️  Using Docker docling-serve (CPU mode)"; \
	if aws sts get-caller-identity --profile $$AWS_PROFILE > /dev/null 2>&1; then \
		echo "✅ AWS credentials valid for profile $$AWS_PROFILE"; \
		eval "$$(aws configure export-credentials --profile $$AWS_PROFILE --format env)" && \
		export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN && \
		docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d; \
	else \
		echo "⚠️  AWS credentials not found for profile $$AWS_PROFILE - Bedrock AI unavailable"; \
		docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d; \
	fi

# Install docling-serve natively (one-time setup for dev-gpu)
docling-install:
	@echo "Installing docling-serve with EasyOCR support..."
	uv tool install "docling-serve[ui,easyocr]"
	@echo ""
	@echo "✅ docling-serve installed. Start it with: make docling-native"

# Start native docling-serve with MPS (Apple Silicon GPU) acceleration
DOCLING_PID_FILE := /tmp/docling-serve.pid
docling-native:
	@if curl -sf http://localhost:5001/health > /dev/null 2>&1; then \
		echo "✅ docling-serve already running on localhost:5001"; \
		exit 0; \
	fi
	@echo "Starting native docling-serve with MPS acceleration..."
	@DOCLING_DEVICE=mps \
	DOCLING_SERVE_LOAD_MODELS_AT_BOOT=true \
	DOCLING_SERVE_ENG_LOC_SHARE_MODELS=true \
	DOCLING_SERVE_MAX_SYNC_WAIT=600 \
	nohup docling-serve run > /tmp/docling-serve.log 2>&1 & echo $$! > $(DOCLING_PID_FILE)
	@echo "docling-serve starting in background (PID: $$(cat $(DOCLING_PID_FILE)))"
	@echo "Logs: /tmp/docling-serve.log"
	@echo "Waiting for health check..."
	@for i in $$(seq 1 60); do \
		if curl -sf http://localhost:5001/health > /dev/null 2>&1; then \
			echo "✅ docling-serve healthy on localhost:5001 (MPS/GPU)"; \
			exit 0; \
		fi; \
		sleep 5; \
	done; \
	echo "⚠️  docling-serve not healthy after 5 minutes — check /tmp/docling-serve.log"; \
	exit 1

# Stop native docling-serve
docling-native-stop:
	@if [ -f $(DOCLING_PID_FILE) ]; then \
		PID=$$(cat $(DOCLING_PID_FILE)); \
		if kill -0 $$PID 2>/dev/null; then \
			kill $$PID; \
			echo "✅ docling-serve stopped (PID: $$PID)"; \
		else \
			echo "docling-serve not running (stale PID file)"; \
		fi; \
		rm -f $(DOCLING_PID_FILE); \
	else \
		echo "No PID file found — docling-serve may not be running"; \
		echo "Try: pkill -f docling-serve"; \
	fi

# Build pipeline viewer (used by dev target)
build-viewer-dev:
	@cd clients/viewer && pnpm install --silent 2>/dev/null && pnpm run build 2>/dev/null || echo "Viewer build skipped"

# Stop services
down:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml down
	@# Stop native docling-serve if running
	@if [ -f /tmp/docling-serve.pid ]; then \
		PID=$$(cat /tmp/docling-serve.pid); \
		if kill -0 $$PID 2>/dev/null; then \
			kill $$PID; \
			echo "Stopped native docling-serve (PID: $$PID)"; \
		fi; \
		rm -f /tmp/docling-serve.pid; \
	fi

# Restart services (down + dev)
restart: down dev

# View logs
logs:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f

# View docling-serve logs
docling-logs:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f docling-serve

# Check docling-serve health
docling-health:
	@curl -sf http://localhost:5001/health && echo " ✅ docling-serve healthy" || echo " ❌ docling-serve unhealthy"

# Health check
health:
	./scripts/health-check.sh

# Run all tests (with parallelization, runs in Docker)
# Note: Excludes concurrent and large file tests which need single-threaded execution
test:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run pytest tests/ -v -n auto --ignore=tests/integration/workflows/test_concurrent_requests.py --ignore=tests/e2e/edge_cases/test_large_files.py

# Run fast unit tests (<30s with parallelization, runs in Docker)
test-fast:
	@echo "Running fast unit tests (<30s with parallelization)..."
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run pytest tests/unit -m unit -v --tb=short --maxfail=10 -n auto

# Alias for test-fast
test-unit: test-fast

# Run integration tests (testcontainers on host, <2min)
test-integration:
	@echo "Running integration tests with testcontainers..."
	@echo "NOTE: Docker Desktop must be running on host machine"
	SKIP_BEDROCK_TESTS=1 uv run pytest tests/integration -m integration -v --tb=short --maxfail=5 --ignore=tests/integration/workflows/test_concurrent_requests.py

# Run concurrent integration tests (requires Docker, single-threaded to avoid resource conflicts)
test-concurrent:
	@echo "Running concurrent integration tests (single-threaded)..."
	@echo "NOTE: These tests use testcontainers and must run without parallelization"
	SKIP_BEDROCK_TESTS=1 uv run pytest tests/integration/workflows/test_concurrent_requests.py -v --tb=short

# Run E2E tests (full workflows, <5min, runs in Docker)
# Excludes large file tests which need single-threaded execution
test-e2e:
	@echo "Running E2E tests (full workflows, <5min)..."
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -e SKIP_BEDROCK_TESTS=1 api-gateway uv run pytest tests/e2e -m slow -v --tb=short --maxfail=3 -n 2 --ignore=tests/e2e/edge_cases/test_large_files.py

# Run large file edge case tests (single-threaded to avoid OOM)
test-large-files:
	@echo "Running large file tests (single-threaded)..."
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -e SKIP_BEDROCK_TESTS=1 api-gateway uv run pytest tests/e2e/edge_cases/test_large_files.py -v --tb=short

# Alias for test-e2e
test-slow: test-e2e

# Run all tests comprehensively
# - Unit tests run in Docker (fast, no external dependencies)
# - Integration tests run on host (use testcontainers which need Docker access)
# - E2E tests run in Docker
test-all:
	@echo "=== Running unit tests in Docker ==="
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -e SKIP_BEDROCK_TESTS=1 api-gateway uv run pytest tests/unit -v -n auto --tb=short
	@echo "\n=== Running integration tests on host (testcontainers) ==="
	@echo "NOTE: These tests use testcontainers and must run on host machine"
	SKIP_BEDROCK_TESTS=1 uv run pytest tests/integration -v --tb=short --ignore=tests/integration/workflows/test_concurrent_requests.py
	@echo "\n=== Running concurrent integration tests (single-threaded) ==="
	@$(MAKE) test-concurrent
	@echo "\n=== Running E2E tests in Docker ==="
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -e SKIP_BEDROCK_TESTS=1 api-gateway uv run pytest tests/e2e -v --tb=short -n 2 --ignore=tests/e2e/edge_cases/test_large_files.py
	@echo "\n=== Running large file tests (single-threaded) ==="
	@$(MAKE) test-large-files
	@echo "\n✅ Complete test suite finished!"

# Redis CLI
redis-cli:
	docker exec -it equalify-reflow-redis redis-cli

# Build Docker images
build:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml build

# Build pipeline viewer (served at the site root /)
build-viewer:
	@echo "Building pipeline viewer..."
	cd clients/viewer && pnpm install && pnpm run build
	@echo ""
	@echo "✅ Pipeline viewer built to clients/viewer/dist/"
	@echo "   Restart with: make down && make dev"
	@echo "   Then access: http://localhost:8080/"

# Access API container shell for debugging
shell:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway /bin/bash

# Run tests inside Docker container (with parallelization)
test-docker:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -e SKIP_BEDROCK_TESTS=1 api-gateway uv run pytest tests/ -v

# View API logs only
logs-api:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f api-gateway

# Cleanup
clean:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml down -v
	docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.native-docling.yml down -v

# Observability URLs
grafana-url:
	@echo "Opening Grafana at http://localhost:3001"
	@echo "Default credentials: admin / admin"
	@open http://localhost:3001 2>/dev/null || xdg-open http://localhost:3001 2>/dev/null || echo "Please open http://localhost:3001 in your browser"

prometheus-url:
	@echo "Opening Prometheus at http://localhost:9090"
	@open http://localhost:9090 2>/dev/null || xdg-open http://localhost:9090 2>/dev/null || echo "Please open http://localhost:9090 in your browser"

metrics-url:
	@echo "Opening API metrics at http://localhost:8080/metrics"
	@open http://localhost:8080/metrics 2>/dev/null || xdg-open http://localhost:8080/metrics 2>/dev/null || echo "Please open http://localhost:8080/metrics in your browser"

# Coverage commands (parallelization with coverage)
coverage:
	@echo "Running tests with coverage (parallelized)..."
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -e SKIP_BEDROCK_TESTS=1 api-gateway sh -c "rm -f .coverage .coverage.* && uv run pytest tests/ --cov=src --cov-report=term-missing --cov-report=html --cov-report=xml -v -n 4"

coverage-html: coverage
	@echo "Opening HTML coverage report..."
	@open htmlcov/index.html 2>/dev/null || xdg-open htmlcov/index.html 2>/dev/null || echo "Please open htmlcov/index.html in your browser"

coverage-report:
	@echo "Coverage summary:"
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run coverage report

# ============================================================================
# Canvas LMS (local development instance)
# ============================================================================
# Canvas lives in a separate repo: ~/Projects/equalify-reflow-canvas/canvas-lms
# The api-gateway joins the canvas-lms_default Docker network (see docker-compose.dev.yml)
# Full setup guide: ../equalify-reflow-canvas/CANVAS-SETUP.md

CANVAS_DIR ?= $(HOME)/Projects/equalify-reflow-canvas/canvas-lms

canvas:
	@echo "Starting Canvas LMS from $(CANVAS_DIR)..."
	@cd $(CANVAS_DIR) && docker compose up -d
	@echo ""
	@echo "Canvas is starting at http://localhost:3000"
	@echo "Run 'make dev' to start the PDF Converter (connects to Canvas automatically)"

canvas-down:
	@echo "Stopping Canvas LMS..."
	@cd $(CANVAS_DIR) && docker compose down

canvas-logs:
	@cd $(CANVAS_DIR) && docker compose logs -f web

# ============================================================================
# LocalStack Debugging (from host)
# ============================================================================
# Note: Rarely needed - most debugging happens via app or docker exec
# This uses AWS CLI from your host machine against LocalStack

localstack-debug:
	@echo "LocalStack debugging commands (from host):"
	@echo ""
	@echo "List S3 buckets:"
	@echo "  AWS_PROFILE=localstack aws s3 ls"
	@echo ""
	@echo "List objects in temp bucket:"
	@echo "  AWS_PROFILE=localstack aws s3 ls s3://equalify-pdf-temp/"
	@echo ""
	@echo "Note: LocalStack must be running (make dev)"
	@echo "Note: Requires ~/.aws/config with a 'localstack' profile (endpoint http://localhost:4566, dummy credentials)"