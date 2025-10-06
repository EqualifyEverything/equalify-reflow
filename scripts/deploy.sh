#!/bin/bash
# Equalify PDF Converter - Deployment Script
# This script builds, tags, and deploys the Docker image to AWS ECS
#
# Usage:
#   ./scripts/deploy.sh [environment] [git-ref]
#
# Examples:
#   ./scripts/deploy.sh production main
#   ./scripts/deploy.sh staging feature-branch
#   ./scripts/deploy.sh production v1.2.3

set -e # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
ENVIRONMENT="${1:-production}"
GIT_REF="${2:-main}"
AWS_REGION="${AWS_REGION:-us-east-1}"
PROJECT_NAME="equalify-pdf"

# Derived values
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
GIT_SHA=$(git rev-parse --short HEAD)
IMAGE_TAG="${GIT_SHA}-${TIMESTAMP}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Equalify PDF Converter - AWS Deployment${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}Environment:${NC} ${ENVIRONMENT}"
echo -e "${GREEN}Git Reference:${NC} ${GIT_REF}"
echo -e "${GREEN}Git SHA:${NC} ${GIT_SHA}"
echo -e "${GREEN}Image Tag:${NC} ${IMAGE_TAG}"
echo -e "${GREEN}AWS Region:${NC} ${AWS_REGION}"
echo ""

# Verify we're on the right branch/ref
echo -e "${YELLOW}➜${NC} Checking out ${GIT_REF}..."
git fetch origin
git checkout ${GIT_REF}

# Verify Terraform outputs exist
if [ ! -f "terraform/terraform.tfstate" ]; then
    echo -e "${RED}✗ Error: Terraform state not found. Run 'terraform apply' first.${NC}"
    exit 1
fi

# Get infrastructure details from Terraform
echo -e "${YELLOW}➜${NC} Getting infrastructure details from Terraform..."
cd terraform
ECR_REPOSITORY=$(terraform output -raw ecr_repository_url)
ECS_CLUSTER=$(terraform output -raw ecs_cluster_name)
ECS_SERVICE=$(terraform output -raw ecs_service_name)
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
cd ..

echo -e "${GREEN}ECR Repository:${NC} ${ECR_REPOSITORY}"
echo -e "${GREEN}ECS Cluster:${NC} ${ECS_CLUSTER}"
echo -e "${GREEN}ECS Service:${NC} ${ECS_SERVICE}"
echo ""

# Run tests before deployment
echo -e "${YELLOW}➜${NC} Running tests..."
if ! make test-fast; then
    echo -e "${RED}✗ Tests failed. Deployment aborted.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Tests passed${NC}"
echo ""

# Build Docker image
echo -e "${YELLOW}➜${NC} Building Docker image..."
docker build \
    --platform linux/amd64 \
    --build-arg BUILD_DATE="${TIMESTAMP}" \
    --build-arg GIT_SHA="${GIT_SHA}" \
    --build-arg GIT_REF="${GIT_REF}" \
    -t ${PROJECT_NAME}:latest \
    -t ${PROJECT_NAME}:${IMAGE_TAG} \
    .

echo -e "${GREEN}✓ Docker image built${NC}"
echo ""

# Login to ECR
echo -e "${YELLOW}➜${NC} Logging in to ECR..."
aws ecr get-login-password --region ${AWS_REGION} | \
    docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com
echo -e "${GREEN}✓ Logged in to ECR${NC}"
echo ""

# Tag and push to ECR
echo -e "${YELLOW}➜${NC} Tagging images for ECR..."
docker tag ${PROJECT_NAME}:latest ${ECR_REPOSITORY}:latest
docker tag ${PROJECT_NAME}:${IMAGE_TAG} ${ECR_REPOSITORY}:${IMAGE_TAG}
echo -e "${GREEN}✓ Images tagged${NC}"
echo ""

echo -e "${YELLOW}➜${NC} Pushing images to ECR..."
docker push ${ECR_REPOSITORY}:latest
docker push ${ECR_REPOSITORY}:${IMAGE_TAG}
echo -e "${GREEN}✓ Images pushed to ECR${NC}"
echo ""

# Update ECS service to trigger deployment
echo -e "${YELLOW}➜${NC} Updating ECS service..."
aws ecs update-service \
    --cluster ${ECS_CLUSTER} \
    --service ${ECS_SERVICE} \
    --force-new-deployment \
    --region ${AWS_REGION} \
    > /dev/null

echo -e "${GREEN}✓ ECS service update triggered${NC}"
echo ""

# Wait for deployment to complete
echo -e "${YELLOW}➜${NC} Waiting for deployment to complete..."
echo -e "${BLUE}This may take 2-5 minutes...${NC}"
echo ""

aws ecs wait services-stable \
    --cluster ${ECS_CLUSTER} \
    --services ${ECS_SERVICE} \
    --region ${AWS_REGION}

echo -e "${GREEN}✓ Deployment complete!${NC}"
echo ""

# Get ALB URL
cd terraform
ALB_URL=$(terraform output -raw alb_url)
cd ..

echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}✓ Deployment Successful!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}Application URL:${NC} ${ALB_URL}"
echo -e "${GREEN}Image Tag:${NC} ${IMAGE_TAG}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Test the application: curl ${ALB_URL}/health"
echo "  2. View logs: aws logs tail /ecs/${PROJECT_NAME} --follow"
echo "  3. Monitor service: aws ecs describe-services --cluster ${ECS_CLUSTER} --services ${ECS_SERVICE}"
echo ""
