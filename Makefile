.PHONY: help dev prod up down logs health test clean

# Default target
help:
	@echo "Equalify PDF Converter - Makefile Commands"
	@echo ""
	@echo "Essential:"
	@echo "  make dev          - Start development environment"
	@echo "  make down         - Stop all services"
	@echo "  make logs         - View service logs"
	@echo "  make health       - Run health checks"
	@echo "  make test         - Run tests"
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

# Cleanup
clean:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml down -v
	docker-compose -f docker-compose.yml -f docker-compose.prod.yml down -v