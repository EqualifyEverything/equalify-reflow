#!/bin/bash

# Equalify PDF Converter - Infrastructure Health Check Script
# Validates that all infrastructure components are running correctly
# Run after: docker-compose -f docker-compose.yml -f docker-compose.dev.yml up

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Counters
PASSED=0
FAILED=0
WARNINGS=0

# Functions for output
print_header() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
}

print_section() {
    echo ""
    echo -e "${BLUE}>>> $1${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
    PASSED=$((PASSED + 1))
}

print_failure() {
    echo -e "${RED}✗${NC} $1"
    FAILED=$((FAILED + 1))
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
    WARNINGS=$((WARNINGS + 1))
}

print_info() {
    echo -e "${BLUE}→${NC} $1"
}

# Function to check if command exists
check_command() {
    command -v "$1" &> /dev/null
}

# Function to check container status
check_container() {
    local container_name=$1
    local friendly_name=$2

    if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        local status=$(docker inspect -f '{{.State.Status}}' "$container_name" 2>/dev/null)
        if [ "$status" = "running" ]; then
            print_success "$friendly_name container is running"
            return 0
        else
            print_failure "$friendly_name container is not running (status: $status)"
            return 1
        fi
    else
        print_failure "$friendly_name container not found"
        return 1
    fi
}

# Function to check container health
check_container_health() {
    local container_name=$1
    local friendly_name=$2

    if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        local health=$(docker inspect -f '{{.State.Health.Status}}' "$container_name" 2>/dev/null || echo "none")
        if [ "$health" = "healthy" ]; then
            print_success "$friendly_name is healthy"
            return 0
        elif [ "$health" = "none" ]; then
            print_warning "$friendly_name has no health check configured"
            return 0
        else
            print_failure "$friendly_name is unhealthy (status: $health)"
            return 1
        fi
    fi
    return 1
}

# Function to test Redis connectivity
test_redis() {
    print_section "Testing Redis Connectivity"

    # Test ping
    if docker exec equalify-pdf-redis redis-cli ping > /dev/null 2>&1; then
        print_success "Redis ping successful"
    else
        print_failure "Redis ping failed"
        return 1
    fi

    # Test basic operations
    if docker exec equalify-pdf-redis redis-cli SET test-key "test-value" > /dev/null 2>&1; then
        print_success "Redis write operation successful"
    else
        print_failure "Redis write operation failed"
        return 1
    fi

    local value=$(docker exec equalify-pdf-redis redis-cli GET test-key 2>/dev/null | tr -d '\r')
    if [ "$value" = "test-value" ]; then
        print_success "Redis read operation successful"
    else
        print_failure "Redis read operation failed (got: $value)"
        return 1
    fi

    docker exec equalify-pdf-redis redis-cli DEL test-key > /dev/null 2>&1

    # Test queue operations
    if docker exec equalify-pdf-redis redis-cli LPUSH eq-pdf:test-queue "test-job" > /dev/null 2>&1; then
        print_success "Redis queue push successful"
    else
        print_failure "Redis queue push failed"
        return 1
    fi

    local job=$(docker exec equalify-pdf-redis redis-cli RPOP eq-pdf:test-queue 2>/dev/null | tr -d '\r')
    if [ "$job" = "test-job" ]; then
        print_success "Redis queue pop successful"
    else
        print_failure "Redis queue pop failed (got: $job)"
        return 1
    fi

    # Show Redis info
    print_info "Redis version: $(docker exec equalify-pdf-redis redis-cli INFO SERVER | grep redis_version | cut -d: -f2 | tr -d '\r')"
    print_info "Redis memory usage: $(docker exec equalify-pdf-redis redis-cli INFO MEMORY | grep used_memory_human | cut -d: -f2 | tr -d '\r')"
    print_info "Redis uptime: $(docker exec equalify-pdf-redis redis-cli INFO SERVER | grep uptime_in_seconds | cut -d: -f2 | tr -d '\r') seconds"

    return 0
}

# Function to test LocalStack S3
test_localstack() {
    print_section "Testing LocalStack S3"

    # Check if awslocal is installed
    if ! check_command awslocal; then
        print_warning "awslocal not installed. Install with: pip install awscli-local"
        return 1
    fi

    # Test S3 connection
    if awslocal s3 ls > /dev/null 2>&1; then
        print_success "LocalStack S3 connection successful"
    else
        print_failure "LocalStack S3 connection failed"
        return 1
    fi

    # Check temp bucket
    if awslocal s3 ls s3://equalify-pdf-temp > /dev/null 2>&1; then
        print_success "Temp bucket (equalify-pdf-temp) exists"
    else
        print_failure "Temp bucket (equalify-pdf-temp) not found"
    fi

    # Check results bucket
    if awslocal s3 ls s3://equalify-pdf-results > /dev/null 2>&1; then
        print_success "Results bucket (equalify-pdf-results) exists"
    else
        print_failure "Results bucket (equalify-pdf-results) not found"
    fi

    # Test upload to temp bucket
    echo "Health check test file" > /tmp/health-check-test.txt
    if awslocal s3 cp /tmp/health-check-test.txt s3://equalify-pdf-temp/health-check-test.txt > /dev/null 2>&1; then
        print_success "S3 upload test successful"

        # Test download
        if awslocal s3 cp s3://equalify-pdf-temp/health-check-test.txt /tmp/health-check-download.txt > /dev/null 2>&1; then
            print_success "S3 download test successful"
            rm -f /tmp/health-check-download.txt
        else
            print_failure "S3 download test failed"
        fi

        # Cleanup
        awslocal s3 rm s3://equalify-pdf-temp/health-check-test.txt > /dev/null 2>&1
    else
        print_failure "S3 upload test failed"
    fi
    rm -f /tmp/health-check-test.txt

    # Show bucket list
    print_info "Available S3 buckets:"
    awslocal s3 ls | sed 's/^/    /'

    return 0
}

# Function to test Docker network
test_network() {
    print_section "Testing Docker Network"

    # Check if network exists
    if docker network ls | grep -q equalify-pdf-network; then
        print_success "Docker network (equalify-pdf-network) exists"
    else
        print_failure "Docker network (equalify-pdf-network) not found"
        return 1
    fi

    # Check network connectivity between containers
    if docker exec equalify-pdf-redis ping -c 1 localstack > /dev/null 2>&1; then
        print_success "Network connectivity: Redis -> LocalStack"
    else
        print_warning "Network connectivity test skipped (expected if LocalStack not running)"
    fi

    return 0
}

# Function to test volumes
test_volumes() {
    print_section "Testing Docker Volumes"

    # Check Redis data volume
    if docker volume ls | grep -q equalify-pdf-redis-data; then
        print_success "Redis data volume exists"
        local size=$(docker volume inspect equalify-pdf-redis-data --format '{{ .Name }}' 2>/dev/null)
        if [ -n "$size" ]; then
            print_info "Redis volume: equalify-pdf-redis-data"
        fi
    else
        print_failure "Redis data volume not found"
    fi

    # Check LocalStack data volume (if dev environment)
    if docker volume ls | grep -q equalify-pdf-localstack-data; then
        print_success "LocalStack data volume exists"
    else
        print_warning "LocalStack data volume not found (expected in dev environment only)"
    fi

    return 0
}

# Main execution
print_header "Equalify PDF Converter - Infrastructure Health Check"

print_info "Starting health checks..."
print_info "Time: $(date)"
echo ""

# Check prerequisites
print_section "Checking Prerequisites"

if check_command docker; then
    print_success "Docker is installed"
else
    print_failure "Docker is not installed"
    exit 1
fi

if check_command docker-compose; then
    print_success "Docker Compose is installed"
else
    print_failure "Docker Compose is not installed"
    exit 1
fi

# Check container status
print_section "Checking Container Status"

check_container "equalify-pdf-redis" "Redis"
check_container "equalify-pdf-api-gateway" "API Gateway"
check_container "equalify-pdf-pii-worker" "PII Worker"
check_container "equalify-pdf-approval-service" "Approval Service"
check_container "equalify-pdf-processing-worker" "Processing Worker"
check_container "equalify-pdf-timeout-worker" "Timeout Worker"

# Check LocalStack (dev only)
if docker ps --format '{{.Names}}' | grep -q equalify-pdf-localstack; then
    check_container "equalify-pdf-localstack" "LocalStack"
fi

# Check container health
print_section "Checking Container Health"

check_container_health "equalify-pdf-redis" "Redis"

if docker ps --format '{{.Names}}' | grep -q equalify-pdf-localstack; then
    check_container_health "equalify-pdf-localstack" "LocalStack"
fi

# Test services
test_redis
test_network
test_volumes

# Test LocalStack if running
if docker ps --format '{{.Names}}' | grep -q equalify-pdf-localstack; then
    test_localstack
else
    print_section "LocalStack Tests"
    print_warning "LocalStack not running (production mode?)"
fi

# Summary
print_header "Health Check Summary"

echo -e "${GREEN}Passed:${NC}   $PASSED"
echo -e "${YELLOW}Warnings:${NC} $WARNINGS"
echo -e "${RED}Failed:${NC}   $FAILED"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}All critical checks passed!${NC}"
    echo -e "${GREEN}========================================${NC}"
    exit 0
else
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}Some checks failed!${NC}"
    echo -e "${RED}========================================${NC}"
    echo ""
    echo -e "${YELLOW}Troubleshooting tips:${NC}"
    echo "  1. Check container logs: docker logs <container-name>"
    echo "  2. Restart services: docker-compose restart"
    echo "  3. View full setup: docker-compose ps"
    echo "  4. Check network: docker network inspect equalify-pdf-network"
    echo ""
    exit 1
fi