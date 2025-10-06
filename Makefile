.PHONY: help dev prod up down logs health test test-fast test-unit test-integration test-e2e test-slow test-all clean build shell test-docker logs-api grafana-url prometheus-url metrics-url coverage coverage-html coverage-report

# Default target
help:
	@echo "Equalify PDF Converter - Makefile Commands"
	@echo ""
	@echo "Essential:"
	@echo "  make dev          - Start development environment"
	@echo "  make down         - Stop all services"
	@echo "  make logs         - View all service logs"
	@echo "  make logs-api     - View API logs only"
	@echo "  make health       - Run health checks"
	@echo "  make test         - Run tests locally"
	@echo ""
	@echo "Testing & Coverage:"
	@echo "  make test         - Run all tests"
	@echo "  make test-fast    - Run fast unit tests (<2min, no Docker needed)"
	@echo "  make test-unit    - Run unit tests only (same as test-fast)"
	@echo "  make test-integration - Run integration tests (real Redis/S3, ~5min)"
	@echo "  make test-e2e     - Run E2E tests (full workflows, ~10min)"
	@echo "  make test-slow    - Run slow/E2E tests (same as test-e2e)"
	@echo "  make test-all     - Run all tests in Docker (comprehensive)"
	@echo "  make coverage     - Run tests with coverage report"
	@echo "  make coverage-html - Generate and open HTML coverage report"
	@echo "  make coverage-report - Show coverage summary"
	@echo ""
	@echo "Docker:"
	@echo "  make build        - Build Docker images"
	@echo "  make shell        - Access API container shell"
	@echo "  make test-docker  - Run tests inside container"
	@echo ""
	@echo "Production:"
	@echo "  make prod         - Start production environment"
	@echo ""
	@echo "Utilities:"
	@echo "  make redis-cli    - Connect to Redis CLI"
	@echo "  make clean        - Remove containers and volumes"
	@echo ""
	@echo "Observability:"
	@echo "  make grafana-url  - Open Grafana (http://localhost:3000)"
	@echo "  make prometheus-url - Open Prometheus (http://localhost:9090)"
	@echo "  make metrics-url  - Open API metrics (http://localhost:8001/metrics)"
	@echo ""

# Development environment
dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Production environment
prod:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Stop services
down:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml down
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down

# View logs
logs:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f

# Health check
health:
	./scripts/health-check.sh

# Run all tests
test:
	uv run pytest tests/ -v

# Run fast unit tests (no Docker needed, <2min)
test-fast:
	@echo "Running fast unit tests (<2min, no Docker needed)..."
	uv run pytest tests/unit -m unit -v --tb=short --maxfail=10

# Alias for test-fast
test-unit: test-fast

# Run integration tests (real Redis/S3 via testcontainers, ~5min)
test-integration:
	@echo "Running integration tests (real Redis/S3, ~5min)..."
	uv run pytest tests/integration -m integration -v --tb=short --maxfail=5

# Run E2E tests (full workflows, ~10min)
test-e2e:
	@echo "Running E2E tests (full workflows, ~10min)..."
	uv run pytest tests/e2e -m slow -v --tb=short --maxfail=3

# Alias for test-e2e
test-slow: test-e2e

# Run all tests in Docker (most comprehensive)
test-all:
	@echo "Running all tests in Docker..."
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run pytest tests/ -v

# Redis CLI
redis-cli:
	docker exec -it equalify-pdf-redis redis-cli

# Build Docker images
build:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml build

# Access API container shell for debugging
shell:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway /bin/bash

# Run tests inside Docker container
test-docker:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run pytest tests/ -v

# View API logs only
logs-api:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f api-gateway

# Cleanup
clean:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml down -v
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v

# Observability URLs
grafana-url:
	@echo "Opening Grafana at http://localhost:3000"
	@echo "Default credentials: admin / admin"
	@open http://localhost:3000 2>/dev/null || xdg-open http://localhost:3000 2>/dev/null || echo "Please open http://localhost:3000 in your browser"

prometheus-url:
	@echo "Opening Prometheus at http://localhost:9090"
	@open http://localhost:9090 2>/dev/null || xdg-open http://localhost:9090 2>/dev/null || echo "Please open http://localhost:9090 in your browser"

metrics-url:
	@echo "Opening API metrics at http://localhost:8001/metrics"
	@open http://localhost:8001/metrics 2>/dev/null || xdg-open http://localhost:8001/metrics 2>/dev/null || echo "Please open http://localhost:8001/metrics in your browser"

# Coverage commands
coverage:
	@echo "Running tests with coverage..."
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run pytest tests/ --cov=src --cov-report=term-missing --cov-report=html --cov-report=xml -v

coverage-html: coverage
	@echo "Opening HTML coverage report..."
	@open htmlcov/index.html 2>/dev/null || xdg-open htmlcov/index.html 2>/dev/null || echo "Please open htmlcov/index.html in your browser"

coverage-report:
	@echo "Coverage summary:"
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run coverage report