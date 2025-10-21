#!/bin/bash

# Equalify PDF Converter - Unified Health Check Script
# Validates infrastructure health in both local and production environments
#
# Usage:
#   ./scripts/health-check.sh           # Check local Docker environment (default)
#   ./scripts/health-check.sh --prod    # Check AWS production deployment
#   ./scripts/health-check.sh --aws     # Same as --prod

set -e

# Parse command line arguments
MODE="local"
while [[ $# -gt 0 ]]; do
    case $1 in
        --prod|--aws|--production)
            MODE="prod"
            shift
            ;;
        --help|-h)
            echo "Equalify PDF Converter - Health Check"
            echo ""
            echo "Usage:"
            echo "  $0              Check local Docker environment (default)"
            echo "  $0 --prod       Check AWS production deployment"
            echo "  $0 --aws        Same as --prod"
            echo "  $0 --help       Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

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

# ============================================================================
# Common Functions
# ============================================================================

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

check_command() {
    command -v "$1" &> /dev/null
}

# ============================================================================
# Local Development Mode Functions
# ============================================================================

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

test_api_health() {
    print_section "Testing API Gateway Health"

    # Check if API is responding
    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
        print_success "API health endpoint responding"

        # Get health status
        local health_response=$(curl -s http://localhost:8080/health)
        local status=$(echo "$health_response" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)

        if [ "$status" = "healthy" ]; then
            print_success "API status: healthy"
        else
            print_failure "API status: $status"
            return 1
        fi

        print_info "Health response: $health_response"
    else
        print_failure "API health endpoint not responding"
        return 1
    fi

    return 0
}

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

test_localstack() {
    print_section "Testing LocalStack S3"

    # Determine which command to use
    local AWS_CMD=""
    if check_command awslocal; then
        AWS_CMD="awslocal"
        print_info "Using awslocal CLI wrapper"
    elif check_command aws; then
        AWS_CMD="AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1 aws --endpoint-url=http://localhost:4566"
        print_info "Using AWS CLI with LocalStack endpoint"
    else
        print_warning "Neither awslocal nor aws CLI found"
        print_info "Skipping LocalStack S3 tests (install AWS CLI to enable)"
        return 0  # Warning, not a failure
    fi

    # Test S3 connection
    if eval $AWS_CMD s3 ls > /dev/null 2>&1; then
        print_success "LocalStack S3 connection successful"
    else
        print_failure "LocalStack S3 connection failed"
        return 1
    fi

    # Check temp bucket
    if eval $AWS_CMD s3 ls s3://equalify-pdf-temp > /dev/null 2>&1; then
        print_success "Temp bucket (equalify-pdf-temp) exists"
    else
        print_failure "Temp bucket (equalify-pdf-temp) not found"
    fi

    # Check results bucket
    if eval $AWS_CMD s3 ls s3://equalify-pdf-results > /dev/null 2>&1; then
        print_success "Results bucket (equalify-pdf-results) exists"
    else
        print_failure "Results bucket (equalify-pdf-results) not found"
    fi

    # Test upload to temp bucket
    echo "Health check test file" > /tmp/health-check-test.txt
    if eval $AWS_CMD s3 cp /tmp/health-check-test.txt s3://equalify-pdf-temp/health-check-test.txt > /dev/null 2>&1; then
        print_success "S3 upload test successful"

        # Test download
        if eval $AWS_CMD s3 cp s3://equalify-pdf-temp/health-check-test.txt /tmp/health-check-download.txt > /dev/null 2>&1; then
            print_success "S3 download test successful"
            rm -f /tmp/health-check-download.txt
        else
            print_failure "S3 download test failed"
        fi

        # Cleanup
        eval $AWS_CMD s3 rm s3://equalify-pdf-temp/health-check-test.txt > /dev/null 2>&1
    else
        print_failure "S3 upload test failed"
    fi
    rm -f /tmp/health-check-test.txt

    # Show bucket list
    print_info "Available S3 buckets:"
    eval $AWS_CMD s3 ls | sed 's/^/    /'

    return 0
}

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

check_local_health() {
    print_header "Equalify PDF Converter - Local Health Check"

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
    check_container "equalify-pdf-api-gateway" "API Gateway (includes all workers)"

    # Check dev environment containers (optional)
    if docker ps --format '{{.Names}}' | grep -q equalify-pdf-localstack; then
        check_container "equalify-pdf-localstack" "LocalStack"
    fi

    if docker ps --format '{{.Names}}' | grep -q equalify-pdf-demo-ui; then
        check_container "equalify-pdf-demo-ui" "Demo UI"
    fi

    if docker ps --format '{{.Names}}' | grep -q equalify-pdf-prometheus; then
        check_container "equalify-pdf-prometheus" "Prometheus"
    fi

    if docker ps --format '{{.Names}}' | grep -q equalify-pdf-grafana; then
        check_container "equalify-pdf-grafana" "Grafana"
    fi

    if docker ps --format '{{.Names}}' | grep -q equalify-pdf-redis-exporter; then
        check_container "equalify-pdf-redis-exporter" "Redis Exporter"
    fi

    # Check container health
    print_section "Checking Container Health"

    check_container_health "equalify-pdf-redis" "Redis"
    check_container_health "equalify-pdf-api-gateway" "API Gateway"

    if docker ps --format '{{.Names}}' | grep -q equalify-pdf-localstack; then
        check_container_health "equalify-pdf-localstack" "LocalStack"
    fi

    if docker ps --format '{{.Names}}' | grep -q equalify-pdf-prometheus; then
        check_container_health "equalify-pdf-prometheus" "Prometheus"
    fi

    if docker ps --format '{{.Names}}' | grep -q equalify-pdf-grafana; then
        check_container_health "equalify-pdf-grafana" "Grafana"
    fi

    # Test services
    test_api_health
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
}

# ============================================================================
# Production/AWS Mode Functions
# ============================================================================

check_prod_health() {
    print_header "Equalify PDF Converter - AWS Production Health Check"

    print_info "Starting AWS health checks..."
    print_info "Time: $(date)"
    echo ""

    # Set AWS profile for all AWS CLI commands (from environment or default)
    export AWS_PROFILE=${AWS_PROFILE:-default}
    print_info "Using AWS profile: $AWS_PROFILE"

    # Check if terraform directory exists
    if [ ! -d "terraform" ]; then
        print_failure "Terraform directory not found. Run from project root."
        exit 1
    fi

    cd terraform

    # Get infrastructure outputs
    print_section "Getting Infrastructure Details"

    if ! terraform output > /dev/null 2>&1; then
        print_failure "Terraform outputs not available. Run 'terraform apply' first."
        cd ..
        exit 1
    fi

    ECS_CLUSTER=$(terraform output -raw ecs_cluster_name 2>/dev/null)
    ECS_SERVICE=$(terraform output -raw ecs_service_name 2>/dev/null)
    ALB_URL=$(terraform output -raw alb_url 2>/dev/null)

    if [ -z "$ECS_CLUSTER" ] || [ -z "$ECS_SERVICE" ] || [ -z "$ALB_URL" ]; then
        print_failure "Could not retrieve terraform outputs"
        cd ..
        exit 1
    fi

    print_info "ECS Cluster: $ECS_CLUSTER"
    print_info "ECS Service: $ECS_SERVICE"
    print_info "ALB URL: $ALB_URL"

    cd ..

    # Check ECS Service Status
    print_section "Checking ECS Service Status"
    local service_info=$(aws ecs describe-services \
        --cluster ${ECS_CLUSTER} \
        --services ${ECS_SERVICE} \
        --region us-east-1 \
        --query 'services[0].{DesiredCount:desiredCount,RunningCount:runningCount,Status:status,Deployments:deployments[*].{Status:status,Running:runningCount,Desired:desiredCount,RolloutState:rolloutState}}' \
        --output json 2>&1)

    if [ $? -eq 0 ]; then
        print_success "ECS service query successful"
        echo "$service_info" | jq '.'
    else
        print_failure "Failed to query ECS service"
        return 1
    fi

    # Check Task Details
    print_section "Checking Task Details"
    TASK_ARNS=$(aws ecs list-tasks \
        --cluster ${ECS_CLUSTER} \
        --service-name ${ECS_SERVICE} \
        --region us-east-1 \
        --query 'taskArns[*]' \
        --output text 2>&1)

    if [ -z "$TASK_ARNS" ]; then
        print_failure "No tasks running!"
    else
        print_success "Found running tasks"
        print_info "Tasks: $TASK_ARNS"

        # Get first task ARN for detailed check
        FIRST_TASK=$(echo $TASK_ARNS | awk '{print $1}')

        print_section "Checking Task Health (first task)"
        local task_info=$(aws ecs describe-tasks \
            --cluster ${ECS_CLUSTER} \
            --tasks ${FIRST_TASK} \
            --region us-east-1 \
            --query 'tasks[0].{LastStatus:lastStatus,HealthStatus:healthStatus,StoppedReason:stoppedReason,Containers:containers[*].{Name:name,Status:lastStatus,Health:healthStatus}}' \
            --output json 2>&1)

        if [ $? -eq 0 ]; then
            print_success "Task health query successful"
            echo "$task_info" | jq '.'
        else
            print_failure "Failed to query task health"
        fi
    fi

    # Check CloudWatch Logs
    print_section "Recent CloudWatch Logs (last 5 minutes)"

    # Disable exit on error for this section
    set +e
    LOG_OUTPUT=$(aws logs tail /ecs/equalify-pdf \
        --since 5m \
        --region us-east-1 \
        --format short 2>&1 | tail -30)
    LOG_EXIT=$?
    set -e

    if [ $LOG_EXIT -eq 0 ]; then
        echo "$LOG_OUTPUT"
        print_success "CloudWatch logs retrieved"
    else
        print_warning "Could not retrieve CloudWatch logs"
        print_info "Error: $LOG_OUTPUT"
    fi

    # Test Health Endpoint
    print_section "Testing Health Endpoint"
    print_info "URL: ${ALB_URL}/health"

    # Disable exit on error for this section
    set +e
    RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" ${ALB_URL}/health 2>&1)
    CURL_EXIT=$?
    set -e

    if [ $CURL_EXIT -ne 0 ]; then
        print_failure "Failed to connect to health endpoint (curl exit code: $CURL_EXIT)"
        print_info "Response: $RESPONSE"
    else
        HTTP_CODE=$(echo "$RESPONSE" | grep "HTTP_CODE:" | cut -d: -f2 || echo "unknown")
        BODY=$(echo "$RESPONSE" | grep -v "HTTP_CODE:" || echo "")

        if [ "$HTTP_CODE" = "200" ]; then
            print_success "Health endpoint returned 200 OK"
            print_info "Response: $BODY"
        elif [ "$HTTP_CODE" = "unknown" ]; then
            print_failure "Could not parse HTTP status code"
            print_info "Raw response: $RESPONSE"
        else
            print_failure "Health endpoint returned HTTP $HTTP_CODE"
            print_info "Response: $BODY"
        fi
    fi

    # Check ALB Target Health
    print_section "Checking ALB Target Health"
    cd terraform

    # Disable exit on error for this section
    set +e
    TARGET_GROUP_ARN=$(terraform output -json deployment_info 2>/dev/null | jq -r '.alb_target_group // empty')
    set -e

    if [ -z "$TARGET_GROUP_ARN" ]; then
        # Fallback: get target group from service
        set +e
        TARGET_GROUP_ARN=$(aws elbv2 describe-target-groups \
            --region us-east-1 \
            --query "TargetGroups[?TargetGroupName=='equalify-pdf-tg'].TargetGroupArn" \
            --output text 2>/dev/null)
        set -e
    fi

    if [ ! -z "$TARGET_GROUP_ARN" ]; then
        set +e
        local target_health=$(aws elbv2 describe-target-health \
            --target-group-arn ${TARGET_GROUP_ARN} \
            --region us-east-1 \
            --query 'TargetHealthDescriptions[*].{Target:Target.Id,Port:Target.Port,State:TargetHealth.State,Reason:TargetHealth.Reason,Description:TargetHealth.Description}' \
            --output json 2>&1)
        local aws_exit=$?
        set -e

        if [ $aws_exit -eq 0 ]; then
            print_success "Target health query successful"
            echo "$target_health" | jq '.' 2>/dev/null || echo "$target_health"
        else
            print_failure "Failed to query target health"
            print_info "Error: $target_health"
        fi
    else
        print_warning "Could not find target group ARN"
    fi

    cd ..
}

# ============================================================================
# Main Execution
# ============================================================================

if [ "$MODE" = "prod" ]; then
    check_prod_health
else
    check_local_health
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
    if [ "$MODE" = "local" ]; then
        echo -e "${YELLOW}Troubleshooting tips:${NC}"
        echo "  1. Check container logs: docker logs <container-name>"
        echo "  2. Restart services: docker-compose restart"
        echo "  3. View full setup: docker-compose ps"
        echo "  4. Check network: docker network inspect equalify-pdf-network"
    else
        echo -e "${YELLOW}Troubleshooting tips:${NC}"
        echo "  1. Check ECS service: aws ecs describe-services --cluster $ECS_CLUSTER --services $ECS_SERVICE"
        echo "  2. View logs: make aws-logs"
        echo "  3. Check task definition: aws ecs describe-task-definition --task-definition equalify-pdf-task"
    fi
    echo ""
    exit 1
fi
