#!/bin/bash

# Equalify PDF Converter - AWS Setup Script
# This script can be run manually to initialize or verify AWS resources
# For LocalStack: Run after docker-compose up
# For Production: Run once to create initial AWS resources

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default to development environment
ENVIRONMENT="${1:-dev}"

# Load environment variables
if [ "$ENVIRONMENT" = "dev" ]; then
    ENV_FILE="$PROJECT_ROOT/.env.dev"
    echo -e "${BLUE}Setting up LocalStack (Development)${NC}"
    AWS_CMD="awslocal"
elif [ "$ENVIRONMENT" = "prod" ]; then
    ENV_FILE="$PROJECT_ROOT/.env.prod"
    echo -e "${BLUE}Setting up AWS (Production)${NC}"
    AWS_CMD="aws"
else
    echo -e "${RED}Error: Invalid environment. Use 'dev' or 'prod'${NC}"
    exit 1
fi

# Check if environment file exists
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}Error: Environment file not found: $ENV_FILE${NC}"
    exit 1
fi

# Source environment variables
set -a
source "$ENV_FILE"
set +a

echo -e "${BLUE}Configuration:${NC}"
echo "  Region: $AWS_DEFAULT_REGION"
echo "  Temp Bucket: $S3_TEMP_BUCKET"
echo "  Results Bucket: $S3_RESULTS_BUCKET"
if [ "$ENVIRONMENT" = "dev" ]; then
    echo "  Endpoint: $AWS_ENDPOINT_URL"
fi
echo ""

# Function to print status
print_status() {
    local status=$1
    local message=$2
    if [ "$status" = "success" ]; then
        echo -e "${GREEN}✓${NC} $message"
    elif [ "$status" = "error" ]; then
        echo -e "${RED}✗${NC} $message"
    elif [ "$status" = "warning" ]; then
        echo -e "${YELLOW}⚠${NC} $message"
    else
        echo -e "${BLUE}→${NC} $message"
    fi
}

# Function to check if command exists
check_command() {
    local cmd=$1
    if ! command -v "$cmd" &> /dev/null; then
        print_status "error" "Command not found: $cmd"
        return 1
    fi
    return 0
}

# Function to wait for service
wait_for_service() {
    local service=$1
    local max_attempts=30
    local attempt=1

    print_status "info" "Waiting for $service to be ready..."

    while [ $attempt -le $max_attempts ]; do
        if [ "$ENVIRONMENT" = "dev" ]; then
            if curl -s "http://localhost:4566/_localstack/health" > /dev/null 2>&1; then
                print_status "success" "$service is ready"
                return 0
            fi
        else
            # For production, just assume AWS is ready
            return 0
        fi

        echo -n "."
        sleep 1
        attempt=$((attempt + 1))
    done

    echo ""
    print_status "error" "$service did not become ready in time"
    return 1
}

# Function to create S3 bucket
create_bucket() {
    local bucket_name=$1
    local public_read=${2:-false}

    print_status "info" "Creating S3 bucket: $bucket_name"

    # Try to create bucket
    if $AWS_CMD s3 mb "s3://$bucket_name" --region "$AWS_DEFAULT_REGION" 2>/dev/null; then
        print_status "success" "Bucket created: $bucket_name"
    else
        # Check if bucket exists
        if $AWS_CMD s3 ls "s3://$bucket_name" > /dev/null 2>&1; then
            print_status "warning" "Bucket already exists: $bucket_name"
        else
            print_status "error" "Failed to create bucket: $bucket_name"
            return 1
        fi
    fi

    # Configure CORS
    print_status "info" "Configuring CORS for: $bucket_name"
    $AWS_CMD s3api put-bucket-cors --bucket "$bucket_name" --cors-configuration '{
        "CORSRules": [
            {
                "AllowedOrigins": ["*"],
                "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
                "AllowedHeaders": ["*"],
                "ExposeHeaders": ["ETag"],
                "MaxAgeSeconds": 3000
            }
        ]
    }' 2>/dev/null || print_status "warning" "CORS configuration failed (may already be set)"

    # Enable versioning
    print_status "info" "Enabling versioning for: $bucket_name"
    $AWS_CMD s3api put-bucket-versioning \
        --bucket "$bucket_name" \
        --versioning-configuration Status=Enabled 2>/dev/null || \
        print_status "warning" "Versioning configuration failed"

    # Configure public read if requested (for results bucket)
    if [ "$public_read" = "true" ]; then
        print_status "info" "Configuring public read access for: $bucket_name"
        $AWS_CMD s3api put-bucket-policy --bucket "$bucket_name" --policy "{
            \"Version\": \"2012-10-17\",
            \"Statement\": [
                {
                    \"Sid\": \"PublicReadGetObject\",
                    \"Effect\": \"Allow\",
                    \"Principal\": \"*\",
                    \"Action\": \"s3:GetObject\",
                    \"Resource\": \"arn:aws:s3:::$bucket_name/*\"
                }
            ]
        }" 2>/dev/null || print_status "warning" "Public read policy configuration failed"
    fi

    return 0
}

# Function to configure lifecycle policy
configure_lifecycle() {
    local bucket_name=$1
    local expiration_days=$2

    print_status "info" "Configuring lifecycle policy for: $bucket_name"
    $AWS_CMD s3api put-bucket-lifecycle-configuration \
        --bucket "$bucket_name" \
        --lifecycle-configuration "{
            \"Rules\": [
                {
                    \"Id\": \"DeleteOldFiles\",
                    \"Status\": \"Enabled\",
                    \"Prefix\": \"\",
                    \"Expiration\": {
                        \"Days\": $expiration_days
                    }
                }
            ]
        }" 2>/dev/null || print_status "warning" "Lifecycle policy configuration failed"
}

# Function to verify bucket
verify_bucket() {
    local bucket_name=$1

    print_status "info" "Verifying bucket: $bucket_name"

    if $AWS_CMD s3 ls "s3://$bucket_name" > /dev/null 2>&1; then
        print_status "success" "Bucket verified: $bucket_name"

        # Test upload
        echo "Test file" > /tmp/test-upload.txt
        if $AWS_CMD s3 cp /tmp/test-upload.txt "s3://$bucket_name/test-upload.txt" 2>/dev/null; then
            print_status "success" "Upload test successful"
            $AWS_CMD s3 rm "s3://$bucket_name/test-upload.txt" 2>/dev/null
        else
            print_status "error" "Upload test failed"
        fi
        rm -f /tmp/test-upload.txt
        return 0
    else
        print_status "error" "Bucket verification failed: $bucket_name"
        return 1
    fi
}

# Main execution
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Equalify PDF Converter - AWS Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check required commands
if [ "$ENVIRONMENT" = "dev" ]; then
    if ! check_command "awslocal"; then
        echo -e "${YELLOW}Installing awslocal...${NC}"
        pip install awscli-local || print_status "error" "Failed to install awslocal"
    fi
    wait_for_service "LocalStack"
else
    check_command "aws" || exit 1
fi

echo ""
echo -e "${BLUE}Creating S3 Buckets...${NC}"
echo ""

# Create temp bucket
create_bucket "$S3_TEMP_BUCKET" false
configure_lifecycle "$S3_TEMP_BUCKET" 7

echo ""

# Create results bucket
create_bucket "$S3_RESULTS_BUCKET" true

echo ""
echo -e "${BLUE}Verifying Setup...${NC}"
echo ""

# Verify buckets
verify_bucket "$S3_TEMP_BUCKET"
verify_bucket "$S3_RESULTS_BUCKET"

# List all buckets
echo ""
echo -e "${BLUE}Available S3 Buckets:${NC}"
$AWS_CMD s3 ls

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}AWS Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

if [ "$ENVIRONMENT" = "dev" ]; then
    echo -e "${BLUE}LocalStack S3 endpoint:${NC} http://localhost:4566"
    echo -e "${BLUE}Test command:${NC} awslocal s3 ls"
else
    echo -e "${BLUE}AWS Region:${NC} $AWS_DEFAULT_REGION"
    echo -e "${BLUE}Test command:${NC} aws s3 ls"
fi

echo ""