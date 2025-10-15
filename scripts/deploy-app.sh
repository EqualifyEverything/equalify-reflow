#!/bin/bash
# Docker Build and Push Script for AWS ECS Deployment
# Builds Docker image and pushes to ECR

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Equalify PDF Converter - Docker Deploy${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Create logs directory if needed
mkdir -p logs
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="logs/docker-deploy-${TIMESTAMP}.log"
echo "📝 Logging session to: $LOG_FILE"
echo ""

# Start logging
exec > >(tee -a "$LOG_FILE")
exec 2>&1

echo "Docker deployment started at: $(date)"
echo ""

# Set AWS profile for all AWS CLI commands (from environment or default)
export AWS_PROFILE=${AWS_PROFILE:-default}

# Get Terraform outputs
echo -e "${BLUE}Step 1: Getting Infrastructure Details${NC}"
cd terraform

# Check if terraform state exists
if [ ! -f terraform.tfstate ]; then
    echo -e "${RED}Error: terraform.tfstate not found${NC}"
    echo -e "${RED}Please run ./scripts/deploy-infrastructure.sh first${NC}"
    exit 1
fi

ECR_REPO=$(terraform output -raw ecr_repository_url)
ECS_CLUSTER=$(terraform output -raw ecs_cluster_name)
ECS_SERVICE=$(terraform output -raw ecs_service_name)
AWS_REGION=$(terraform output -json deployment_info | jq -r '.region')
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

cd ..

echo -e "${GREEN}✓ Infrastructure details:${NC}"
echo "  ECR Repository: $ECR_REPO"
echo "  ECS Cluster: $ECS_CLUSTER"
echo "  ECS Service: $ECS_SERVICE"
echo "  AWS Region: $AWS_REGION"
echo "  AWS Account: $AWS_ACCOUNT_ID"
echo ""

# Login to ECR
echo -e "${BLUE}Step 2: Authenticating with ECR${NC}"
aws ecr get-login-password --region ${AWS_REGION} | \
    docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

echo -e "${GREEN}✓ ECR authentication successful${NC}"
echo ""

# Build Docker image
echo -e "${BLUE}Step 3: Building Docker Image${NC}"
echo "⏳ This may take 3-5 minutes..."
echo ""

# Build for linux/amd64 (required for ECS Fargate)
docker build --platform linux/amd64 -t equalify-pdf:latest .

echo -e "${GREEN}✓ Docker image built successfully${NC}"
echo ""

# Tag image
echo -e "${BLUE}Step 4: Tagging Image${NC}"
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
IMAGE_TAG="${TIMESTAMP}-${GIT_SHA}"

docker tag equalify-pdf:latest ${ECR_REPO}:latest
docker tag equalify-pdf:latest ${ECR_REPO}:${IMAGE_TAG}

echo -e "${GREEN}✓ Image tagged:${NC}"
echo "  - ${ECR_REPO}:latest"
echo "  - ${ECR_REPO}:${IMAGE_TAG}"
echo ""

# Push to ECR
echo -e "${BLUE}Step 5: Pushing to ECR${NC}"
echo "⏳ This may take 2-4 minutes..."
echo ""

docker push ${ECR_REPO}:latest
docker push ${ECR_REPO}:${IMAGE_TAG}

echo -e "${GREEN}✓ Images pushed to ECR${NC}"
echo ""

# Update ECS service
echo -e "${BLUE}Step 6: Deploying to ECS${NC}"
echo "⏳ Triggering ECS deployment..."
echo ""

aws ecs update-service \
    --cluster ${ECS_CLUSTER} \
    --service ${ECS_SERVICE} \
    --force-new-deployment \
    --region ${AWS_REGION} \
    --no-cli-pager

echo -e "${GREEN}✓ ECS deployment triggered${NC}"
echo ""

# Wait for deployment
echo -e "${BLUE}Step 7: Waiting for Deployment${NC}"
echo "⏳ This may take 3-5 minutes..."
echo "You can watch progress in AWS Console:"
echo "https://us-east-1.console.aws.amazon.com/ecs/v2/clusters/${ECS_CLUSTER}/services/${ECS_SERVICE}"
echo ""

# Wait for service to stabilize
aws ecs wait services-stable \
    --cluster ${ECS_CLUSTER} \
    --services ${ECS_SERVICE} \
    --region ${AWS_REGION}

echo -e "${GREEN}✓ Deployment complete!${NC}"
echo ""

# Get ALB URL from Terraform
cd terraform
ALB_URL=$(terraform output -raw alb_url)
cd ..

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment Successful!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Application URL:${NC}"
echo "  $ALB_URL"
echo ""
echo -e "${BLUE}Test Health Endpoint:${NC}"
echo "  curl $ALB_URL/health"
echo ""
echo -e "${BLUE}View API Documentation:${NC}"
echo "  open $ALB_URL/docs"
echo ""
echo -e "${BLUE}View Logs:${NC}"
echo "  aws logs tail /ecs/equalify-pdf --follow"
echo ""
echo "Deployment completed at: $(date)"
echo "📝 Full log saved to: $LOG_FILE"
