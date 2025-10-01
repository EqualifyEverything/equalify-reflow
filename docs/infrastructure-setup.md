# Equalify PDF Converter - Infrastructure Setup Guide

This guide walks you through setting up the complete infrastructure for the Equalify PDF Converter, both for local development and production deployment.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Quick Start (Development)](#quick-start-development)
- [Infrastructure Components](#infrastructure-components)
- [Development Setup](#development-setup)
- [Production Setup](#production-setup)
- [Validation](#validation)
- [Troubleshooting](#troubleshooting)
- [Common Operations](#common-operations)

## Overview

The Equalify PDF Converter uses a **monolith with background task queue** architecture:

- **Single Python Application**: FastAPI + background workers in one containerized process
- **Redis**: Message queue and caching
- **LocalStack**: Local AWS services (S3, CloudWatch) for development
- **Docker Compose**: Container orchestration
- **S3**: PDF storage and HTML results hosting
- **Background Workers**: PII detection, approval workflow, AI processing, timeout monitoring (all run as threads within the monolith)

## Prerequisites

### Required Software

- **Docker** (v20.10+)
  ```bash
  docker --version
  ```

- **Docker Compose** (v2.0+)
  ```bash
  docker-compose --version
  ```

- **Git**
  ```bash
  git --version
  ```

### Development Tools (Recommended)

- **awslocal** (for LocalStack interactions)
  ```bash
  pip install awscli-local
  ```

- **redis-cli** (for Redis debugging)
  ```bash
  # Install via package manager
  # macOS: brew install redis
  # Ubuntu: apt-get install redis-tools
  ```

## Quick Start (Development)

Get up and running in under 2 minutes:

```bash
# 1. Clone the repository
cd /path/to/equalify-pdf-converter

# 2. Start entire stack (one command!)
make dev

# 3. Verify API is running
curl http://localhost:8000/health

# 4. Access API documentation
open http://localhost:8000/docs
```

That's it! The entire stack (Redis, LocalStack, FastAPI) runs in Docker with unified networking.

### What Just Happened?

- **Redis**: Started and waiting on port 6379
- **LocalStack**: S3 buckets created automatically (equalify-pdf-temp, equalify-pdf-results)
- **FastAPI API**: Running on port 8000 with hot-reload enabled
- **Background Workers**: Will run as threads within the API container (Phase 2)

## Infrastructure Components

### 1. Redis

**Purpose**: Message queue and caching layer

**Queues**:
- `eq-pdf:queue:pii` - PII detection jobs
- `eq-pdf:queue:approval` - Faculty approval workflow
- `eq-pdf:queue:processing` - AI processing jobs

**Data Structures**:
- Lists for job queues
- Sorted sets for timeout tracking
- Hashes for job metadata

**Configuration**: `infrastructure/redis/redis.conf`

### 2. LocalStack (Development Only)

**Purpose**: Local AWS cloud emulation

**Services**:
- S3 for file storage
- CloudWatch for logging

**S3 Buckets**:
- `equalify-pdf-temp` - Temporary PDF uploads (7-day lifecycle)
- `equalify-pdf-results` - Processed HTML results (public read, versioned)

**Configuration**: `infrastructure/localstack/init-aws.sh`

### 3. FastAPI Monolith Application (Containerized)

**Purpose**: Single Python application running all API and background processing

**Container**: `api-gateway` (running on port 8000)

**Components** (all in one process):
- **FastAPI REST API**: Document submission, status tracking, results retrieval
- **PII Worker**: Microsoft Presidio PII detection (Phase 2 - background thread)
- **Processing Worker**: PydanticAI multi-agent processing (Phase 2 - background thread)
- **Timeout Worker**: Approval timeout monitoring (Phase 2 - background thread)

**Current Status**: API endpoints implemented and containerized with hot-reload for development

## Development Setup

### Step 1: Start the Stack

The `.env.dev` file is already configured with sensible defaults. Simply start everything:

```bash
# Start entire stack (Redis, LocalStack, FastAPI)
make dev

# Or use docker-compose directly
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

View logs:

```bash
# All services
make logs

# API logs only
make logs-api

# Or use docker-compose
docker-compose -f docker-compose.yml -f docker-compose.dev.yml logs -f api-gateway
```

### Step 2: Verify Setup

Check that the API is responding:

```bash
# Health check endpoint
curl http://localhost:8000/health

# Should return:
# {
#   "status": "healthy",
#   "checks": {
#     "redis": true,
#     "s3": true,
#     "queue_depth": 0
#   }
# }
```

### Step 3: Development Workflow

**Hot Reload**: The API automatically reloads when you edit code in `src/`:

```bash
# Edit any file in src/
vim src/api/documents.py

# Changes are immediately reflected (no rebuild needed!)
```

**Run Tests**:

```bash
# Run tests inside the container (same environment as production)
make test-docker

# Or run tests locally (requires uv)
make test
```

**Access Container Shell**:

```bash
# Debug inside the API container
make shell

# You're now inside the container with access to:
# - uv run python (Python with all dependencies)
# - redis-cli (connect to Redis)
# - awslocal (interact with LocalStack)
```

**Optional: Customize Configuration**

If you need to change settings, edit `.env.dev`:

```bash
# AWS Configuration (LocalStack) - uses Docker DNS
AWS_ENDPOINT_URL=http://localstack:4566
REDIS_URL=redis://redis:6379

# Application
LOG_LEVEL=DEBUG
ENVIRONMENT=development
```

### Advanced: Manual Infrastructure Testing

These commands are useful for debugging but not required for normal development:

#### Test Redis

```bash
# Connect to Redis
docker exec -it equalify-pdf-redis redis-cli

# Test commands
PING
SET test "hello"
GET test
LPUSH eq-pdf:queue:pii "test-job"
RPOP eq-pdf:queue:pii
```

#### Test LocalStack S3

```bash
# List buckets
awslocal s3 ls

# Upload test file
echo "Test content" > test.txt
awslocal s3 cp test.txt s3://equalify-pdf-temp/test.txt

# Download test file
awslocal s3 cp s3://equalify-pdf-temp/test.txt downloaded.txt

# List objects
awslocal s3 ls s3://equalify-pdf-temp/

# Delete test file
awslocal s3 rm s3://equalify-pdf-temp/test.txt
rm test.txt downloaded.txt
```

## Production Setup

### Step 1: Configure AWS Credentials

Production uses real AWS services. Configure credentials:

```bash
# Option 1: AWS IAM Role (recommended for ECS)
# Attach IAM role to ECS task definition

# Option 2: Environment variables
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_DEFAULT_REGION=us-east-1
```

### Step 2: Create Production Environment File

```bash
cp .env.example .env.prod
```

Edit `.env.prod` with production values:

```bash
# AWS Configuration (Real AWS)
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
AWS_DEFAULT_REGION=us-east-1
# AWS_ENDPOINT_URL not set - uses real AWS

# S3 Buckets (must exist in AWS)
S3_TEMP_BUCKET=equalify-pdf-temp
S3_RESULTS_BUCKET=equalify-pdf-results

# Redis (AWS ElastiCache endpoint)
REDIS_URL=redis://your-elasticache-endpoint:6379

# Application
ENVIRONMENT=production
LOG_LEVEL=INFO
```

### Step 3: Create AWS Resources

Run the setup script to create S3 buckets in AWS:

```bash
# Ensure AWS credentials are configured
aws configure list

# Create production resources
./scripts/setup-aws.sh prod
```

### Step 4: Deploy to AWS ECS

Production deployment uses AWS ECS with Fargate:

```bash
# Start services with production configuration
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Note: For actual AWS ECS deployment, you'll need to:
1. Build and push Docker images to ECR
2. Create ECS task definitions
3. Configure ECS services
4. Set up load balancers
5. Configure auto-scaling

(This will be covered in Phase 2 deployment documentation)

## Validation

### Health Check Script

The comprehensive health check validates:

```bash
./scripts/health-check.sh
```

Checks performed:
- Container status (running/stopped)
- Container health (health checks)
- Redis connectivity (ping, read/write, queue operations)
- LocalStack S3 (connection, bucket existence, upload/download)
- Docker network connectivity
- Volume persistence

### Manual Validation

#### Check Container Status

```bash
# List all containers
docker ps -a

# Check specific container
docker ps -f name=equalify-pdf-redis

# View container logs
docker logs equalify-pdf-redis
docker logs equalify-pdf-localstack
```

#### Check Redis

```bash
# Test connectivity
docker exec equalify-pdf-redis redis-cli ping

# Get Redis info
docker exec equalify-pdf-redis redis-cli INFO

# Monitor Redis commands
docker exec equalify-pdf-redis redis-cli MONITOR
```

#### Check LocalStack

```bash
# Check LocalStack health
curl http://localhost:4566/_localstack/health

# List S3 buckets
awslocal s3 ls

# Check bucket contents
awslocal s3 ls s3://equalify-pdf-temp/
```

#### Check Network

```bash
# Inspect network
docker network inspect equalify-pdf-network

# Test connectivity from one container to another
docker exec equalify-pdf-redis ping localstack -c 3
```

## Troubleshooting

### Common Issues

#### 1. Containers Not Starting

**Problem**: Services fail to start or restart repeatedly

**Solutions**:
```bash
# Check logs
docker-compose -f docker-compose.yml -f docker-compose.dev.yml logs

# Check disk space
df -h

# Remove old containers and volumes
docker-compose -f docker-compose.yml -f docker-compose.dev.yml down -v
docker system prune -a

# Restart Docker daemon
# macOS: restart Docker Desktop
# Linux: sudo systemctl restart docker
```

#### 2. Redis Connection Refused

**Problem**: Cannot connect to Redis

**Solutions**:
```bash
# Check if Redis is running
docker ps | grep redis

# Check Redis logs
docker logs equalify-pdf-redis

# Test connectivity
docker exec equalify-pdf-redis redis-cli ping

# Restart Redis
docker-compose -f docker-compose.yml -f docker-compose.dev.yml restart redis
```

#### 3. LocalStack S3 Buckets Not Created

**Problem**: S3 buckets don't exist after starting LocalStack

**Solutions**:
```bash
# Check LocalStack logs
docker logs equalify-pdf-localstack

# Check if init script ran
docker exec equalify-pdf-localstack ls -l /etc/localstack/init/ready.d/

# Manually run init script
docker exec equalify-pdf-localstack /etc/localstack/init/ready.d/init-aws.sh

# Or use setup script
./scripts/setup-aws.sh dev
```

#### 4. awslocal Command Not Found

**Problem**: Cannot run awslocal commands

**Solution**:
```bash
# Install awslocal
pip install awscli-local

# Or use aws with endpoint
aws --endpoint-url=http://localhost:4566 s3 ls
```

#### 5. Port Already in Use

**Problem**: Port 6379 or 4566 already in use

**Solutions**:
```bash
# Find process using port
lsof -i :6379
lsof -i :4566

# Kill process or change port in docker-compose.yml
# Edit ports section: "6380:6379" instead of "6379:6379"
```

#### 6. Permission Denied on Scripts

**Problem**: Cannot execute setup or health check scripts

**Solution**:
```bash
# Make scripts executable
chmod +x scripts/setup-aws.sh
chmod +x scripts/health-check.sh
chmod +x infrastructure/localstack/init-aws.sh
```

### Debug Mode

Enable verbose logging:

```bash
# Edit .env.dev
LOG_LEVEL=DEBUG
DEBUG=1

# Restart services
docker-compose -f docker-compose.yml -f docker-compose.dev.yml restart
```

### Reset Everything

Complete reset of infrastructure:

```bash
# Stop all services
docker-compose -f docker-compose.yml -f docker-compose.dev.yml down

# Remove volumes (WARNING: deletes all data)
docker volume rm equalify-pdf-redis-data
docker volume rm equalify-pdf-localstack-data

# Remove network
docker network rm equalify-pdf-network

# Restart from scratch
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

## Common Operations

### Essential Commands (via Makefile)

```bash
# Start development environment
make dev

# Stop all services
make down

# View all logs
make logs

# View API logs only
make logs-api

# Run tests in container
make test-docker

# Access container shell for debugging
make shell

# Build Docker images
make build

# Connect to Redis CLI
make redis-cli

# Run health checks
make health

# Clean up (removes containers and volumes)
make clean
```

### Advanced Docker Compose Commands

If you need more control, use docker-compose directly:

```bash
# Start with logs visible
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up

# Restart specific service
docker-compose -f docker-compose.yml -f docker-compose.dev.yml restart api-gateway

# View logs with timestamps
docker-compose -f docker-compose.yml -f docker-compose.dev.yml logs -f --timestamps

# See resource usage
docker-compose -f docker-compose.yml -f docker-compose.dev.yml ps
docker stats
```

### Update Configuration

```bash
# Edit configuration files
vim .env.dev
vim infrastructure/redis/redis.conf

# Recreate containers with new configuration
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate
```

### Backup and Restore

#### Backup Redis Data

```bash
# Trigger Redis save
docker exec equalify-pdf-redis redis-cli BGSAVE

# Wait for save to complete
docker exec equalify-pdf-redis redis-cli LASTSAVE

# Copy backup
docker cp equalify-pdf-redis:/data/dump.rdb ./redis-backup-$(date +%Y%m%d).rdb
```

#### Restore Redis Data

```bash
# Stop Redis
docker-compose -f docker-compose.yml -f docker-compose.dev.yml stop redis

# Copy backup to container
docker cp ./redis-backup.rdb equalify-pdf-redis:/data/dump.rdb

# Start Redis
docker-compose -f docker-compose.yml -f docker-compose.dev.yml start redis
```

#### Backup LocalStack Data

```bash
# Export LocalStack data
docker run --rm -v equalify-pdf-localstack-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/localstack-backup-$(date +%Y%m%d).tar.gz -C /data .
```

#### Restore LocalStack Data

```bash
# Stop LocalStack
docker-compose -f docker-compose.yml -f docker-compose.dev.yml stop localstack

# Import data
docker run --rm -v equalify-pdf-localstack-data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/localstack-backup.tar.gz -C /data

# Start LocalStack
docker-compose -f docker-compose.yml -f docker-compose.dev.yml start localstack
```

### Scale Services

```bash
# Scale processing workers (production)
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d --scale processing-worker=3

# Note: Only stateless services can be scaled
```

### Monitor Resources

```bash
# Container resource usage
docker stats

# Specific container
docker stats equalify-pdf-redis

# Disk usage
docker system df

# Volume usage
docker volume ls
docker volume inspect equalify-pdf-redis-data
```

## Next Steps

**Current Status**: Phase 2 - Core Services (In Progress)

✅ **Completed**:
- Infrastructure Foundation (Redis, LocalStack, Docker Compose)
- Shared Data Models (Pydantic models, queue schemas)
- Shared Services (Storage, Queue, Job management)
- Document API Endpoints (POST /submit, GET /status, GET /result)
- Docker Containerization (Hot-reload, unified networking)

🚧 **Next Tasks**:
1. **PRD-005**: Implement PII Worker with Microsoft Presidio
2. **PRD-006**: Implement Approval Workflow API
3. **PRD-007**: Implement Processing Worker with PydanticAI
4. **PRD-008**: Implement Timeout Worker
5. **Phase 3**: End-to-end integration testing
6. **Phase 4**: Deploy to AWS ECS

## Additional Resources

- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [LocalStack Documentation](https://docs.localstack.cloud/)
- [Redis Documentation](https://redis.io/documentation)
- [AWS S3 Documentation](https://docs.aws.amazon.com/s3/)
- [Project Architecture](../CLAUDE.md)

## Support

If you encounter issues not covered in this guide:

1. Check container logs: `make logs` or `make logs-api`
2. Access container shell for debugging: `make shell`
3. Run health check: `curl http://localhost:8000/health`
4. Review troubleshooting section above
5. Check GitHub issues or create a new one

---

**Last Updated**: 2025-10-01
**Version**: 2.0.0 (Phase 2 - Containerized Application with Hot-Reload)