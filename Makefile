.PHONY: help dev prod up down logs health test clean build shell test-docker logs-api

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

# Development environment
dev:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Production environment
prod:
	docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Stop services
down:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml down
	docker-compose -f docker-compose.yml -f docker-compose.prod.yml down

# View logs
logs:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml logs -f

# Health check
health:
	./scripts/health-check.sh

# Run tests
test:
	uv run pytest tests/ -v

# Redis CLI
redis-cli:
	docker exec -it equalify-pdf-redis redis-cli

# Build Docker images
build:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml build

# Access API container shell for debugging
shell:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway /bin/bash

# Run tests inside Docker container
test-docker:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run pytest tests/ -v

# View API logs only
logs-api:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml logs -f api-gateway

# Cleanup
clean:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml down -v
	docker-compose -f docker-compose.yml -f docker-compose.prod.yml down -v