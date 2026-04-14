# Equalify PDF Converter - Scripts

This directory contains utility scripts for managing the Equalify PDF Converter infrastructure.

## Available Scripts

### setup-aws.sh

Initializes AWS resources (S3 buckets) for development or production environments.

**Usage**:
```bash
# Development (LocalStack)
./scripts/setup-aws.sh dev

# Production (Real AWS)
./scripts/setup-aws.sh prod
```

**What it does**:
- Creates S3 buckets: `equalify-pdf-temp`, `equalify-pdf-results`
- Configures CORS for both buckets
- Sets public read policy on results bucket
- Enables versioning on results bucket
- Configures lifecycle policy (7-day expiration) on temp bucket
- Verifies bucket creation with upload/download tests

**Requirements**:
- Development: `awslocal` (install with `pip install awscli-local`)
- Production: `aws` CLI with configured credentials

**Environment Variables** (loaded from `.env.dev` or `.env.prod`):
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_DEFAULT_REGION`
- `AWS_ENDPOINT_URL` (dev only)
- `S3_TEMP_BUCKET`
- `S3_RESULTS_BUCKET`

### health-check.sh

Validates that all infrastructure components are running correctly.

**Usage**:
```bash
# Run health check
./scripts/health-check.sh
```

**What it checks**:
- ✓ Docker and Docker Compose installation
- ✓ Container status (running/stopped)
- ✓ Container health checks
- ✓ Redis connectivity (ping, read/write, queue operations)
- ✓ LocalStack S3 (connection, buckets, upload/download)
- ✓ Docker network connectivity
- ✓ Volume persistence

**Output**:
```
========================================
Equalify PDF Converter - Infrastructure Health Check
========================================

>>> Checking Prerequisites
✓ Docker is installed
✓ Docker Compose is installed

>>> Checking Container Status
✓ Redis container is running
✓ API Gateway container is running
...

========================================
Health Check Summary
========================================
Passed:   15
Warnings: 2
Failed:   0

========================================
All critical checks passed!
========================================
```

**Exit Codes**:
- `0`: All critical checks passed
- `1`: One or more checks failed

**Requirements**:
- Docker containers must be running
- Development: `awslocal` for S3 checks

## Script Examples

### Quick Setup Development Environment

```bash
# 1. Start services
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# 2. Wait for services to start
sleep 30

# 3. Initialize AWS resources (optional, runs automatically)
./scripts/setup-aws.sh dev

# 4. Verify everything works
./scripts/health-check.sh
```

### Debug Issues

```bash
# Run health check to identify problems
./scripts/health-check.sh

# Check specific container logs
docker logs equalify-pdf-redis
docker logs equalify-pdf-localstack

# Recreate resources
./scripts/setup-aws.sh dev

# Full reset
docker-compose -f docker-compose.yml -f docker-compose.dev.yml down -v
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d
sleep 30
./scripts/health-check.sh
```

### Continuous Integration

```bash
#!/bin/bash
# CI pipeline example

set -e

# Start services
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Wait for services
sleep 30

# Initialize resources
./scripts/setup-aws.sh dev

# Verify infrastructure
./scripts/health-check.sh

# Run tests
# ... your test commands ...

# Cleanup
docker-compose -f docker-compose.yml -f docker-compose.dev.yml down -v
```

## Troubleshooting

### Script Won't Execute

```bash
# Make scripts executable
chmod +x scripts/setup-aws.sh
chmod +x scripts/health-check.sh
```

### awslocal Not Found

```bash
# Install awslocal
pip install awscli-local

# Or use aws with endpoint
aws --endpoint-url=http://localhost:4566 s3 ls
```

### Health Check Fails

Common causes:
1. Services not fully started - wait longer
2. Ports in use - check with `lsof -i :6379` and `lsof -i :4566`
3. Docker out of resources - check with `docker system df`
4. Configuration errors - check logs with `docker logs <container-name>`

### LocalStack Not Ready

```bash
# Check LocalStack health manually
curl http://localhost:4566/_localstack/health

# Check logs
docker logs equalify-pdf-localstack

# Restart LocalStack
docker-compose -f docker-compose.yml -f docker-compose.dev.yml restart localstack

# Wait longer and retry
sleep 30
./scripts/setup-aws.sh dev
```

## Script Maintenance

### Adding New Checks to health-check.sh

```bash
# Template for new check function
test_new_service() {
    print_section "Testing New Service"

    if some_test_command; then
        print_success "Test passed"
    else
        print_failure "Test failed"
        return 1
    fi

    return 0
}

# Add to main execution
test_new_service
```

### Adding New Resources to setup-aws.sh

```bash
# Template for new resource creation
create_new_resource() {
    local resource_name=$1

    print_status "info" "Creating resource: ${resource_name}"

    if $AWS_CMD create-resource "$resource_name"; then
        print_status "success" "Resource created: $resource_name"
        return 0
    else
        print_status "error" "Resource creation failed: $resource_name"
        return 1
    fi
}

# Add to main execution
create_new_resource "my-resource"
```

## Future Scripts

Planned scripts for future phases:

- `build-images.sh` - Build and tag Docker images
- `push-images.sh` - Push images to ECR
- `deploy-ecs.sh` - Deploy to AWS ECS
- `backup.sh` - Backup Redis and configuration data
- `restore.sh` - Restore from backup
- `monitor.sh` - Real-time monitoring dashboard
- `benchmark.sh` - Performance testing

## Additional Resources

- [Infrastructure Setup Guide](../docs/infrastructure-setup.md)
- [Docker Compose Files](../docker-compose.yml)
- [Environment Configuration](../.env.example)

---

**Note**: Always test scripts in development before using in production!