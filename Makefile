.PHONY: help dev prod up down logs health test test-fast test-unit test-integration test-e2e test-slow test-all clean build shell test-docker logs-api grafana-url prometheus-url metrics-url coverage coverage-html coverage-report aws-health aws-logs aws-status aws-deploy aws-shell localstack-debug

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
	@echo "  make test-fast    - Run fast unit tests in Docker (<30s with parallelization)"
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
	@echo "  make metrics-url  - Open API metrics (http://localhost:8080/metrics)"
	@echo ""
	@echo "AWS Operations (requires AWS_PROFILE=uic or aws sso login):"
	@echo "  make aws-health   - Check AWS deployment health"
	@echo "  make aws-logs     - Tail CloudWatch logs"
	@echo "  make aws-status   - Show ECS service status"
	@echo "  make aws-deploy   - Deploy to AWS (infrastructure + Docker)"
	@echo ""
	@echo "Debugging:"
	@echo "  make localstack-debug - Debug LocalStack from host (rarely needed)"
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

# Run all tests (with parallelization, runs in Docker)
test:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run pytest tests/ -v -n auto

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
	uv run pytest tests/integration -m integration -v --tb=short --maxfail=5

# Run E2E tests (full workflows, <5min, runs in Docker)
test-e2e:
	@echo "Running E2E tests (full workflows, <5min)..."
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run pytest tests/e2e -m slow -v --tb=short --maxfail=3 -n 2

# Alias for test-e2e
test-slow: test-e2e

# Run all tests in Docker (most comprehensive, <2min with parallelization)
test-all:
	@echo "Running all tests in Docker with parallelization..."
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway sh -c "rm -f .coverage .coverage.* && uv run pytest tests/ -v -n 4"

# Redis CLI
redis-cli:
	docker exec -it equalify-pdf-redis redis-cli

# Build Docker images
build:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml build

# Access API container shell for debugging
shell:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway /bin/bash

# Run tests inside Docker container (with parallelization)
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
	@echo "Opening API metrics at http://localhost:8080/metrics"
	@open http://localhost:8080/metrics 2>/dev/null || xdg-open http://localhost:8080/metrics 2>/dev/null || echo "Please open http://localhost:8080/metrics in your browser"

# Coverage commands (parallelization with coverage)
coverage:
	@echo "Running tests with coverage (parallelized)..."
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway sh -c "rm -f .coverage .coverage.* && uv run pytest tests/ --cov=src --cov-report=term-missing --cov-report=html --cov-report=xml -v -n 4"

coverage-html: coverage
	@echo "Opening HTML coverage report..."
	@open htmlcov/index.html 2>/dev/null || xdg-open htmlcov/index.html 2>/dev/null || echo "Please open htmlcov/index.html in your browser"

coverage-report:
	@echo "Coverage summary:"
	docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run coverage report

# ============================================================================
# AWS Operations (uses AWS_PROFILE from environment or .env)
# ============================================================================
# Note: These commands use AWS CLI profiles from ~/.aws/config
# Setup: 1) Set AWS_PROFILE in .env, 2) Configure profile in ~/.aws/config
# Default profile: "default" (change AWS_PROFILE in .env to use a different profile)

AWS_PROFILE ?= default

aws-health:
	@echo "Checking AWS deployment health..."
	AWS_PROFILE=$(AWS_PROFILE) ./scripts/health-check.sh --prod

aws-logs:
	@echo "Tailing CloudWatch logs (Ctrl+C to exit)..."
	AWS_PROFILE=$(AWS_PROFILE) aws logs tail /ecs/equalify-pdf --follow --region us-east-1

aws-status:
	@echo "ECS Service Status:"
	@AWS_PROFILE=$(AWS_PROFILE) aws ecs describe-services \
		--cluster equalify-pdf-cluster \
		--services equalify-pdf-service \
		--region us-east-1 \
		--query 'services[0].{Desired:desiredCount,Running:runningCount,Status:status,Deployment:deployments[0].rolloutState}' \
		--output table

aws-deploy:
	@echo "Deploying to AWS..."
	@./scripts/deploy-infrastructure.sh && ./scripts/deploy-app.sh

aws-shell:
	@echo "Connecting to ECS container..."
	@TASK_ARN=$$(AWS_PROFILE=$(AWS_PROFILE) aws ecs list-tasks \
		--cluster equalify-pdf-cluster \
		--service-name equalify-pdf-service \
		--region us-east-1 \
		--query 'taskArns[0]' \
		--output text) && \
	AWS_PROFILE=$(AWS_PROFILE) aws ecs execute-command \
		--cluster equalify-pdf-cluster \
		--task $$TASK_ARN \
		--container app \
		--interactive \
		--command "/bin/bash"

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
	@echo "Note: Requires ~/.aws/config with localstack profile (see .aws-config-example)"