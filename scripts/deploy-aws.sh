#!/bin/bash
# AWS Deployment Helper Script
# This script ensures clean AWS environment (no LocalStack endpoint)

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Equalify PDF Converter - AWS Deployment${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Create logs directory if it doesn't exist
mkdir -p logs
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="logs/deploy-${TIMESTAMP}.log"
echo "📝 Logging session to: $LOG_FILE"
echo ""

# Start logging (tee allows output to both terminal and log file)
exec > >(tee -a "$LOG_FILE")
exec 2>&1

echo "Deployment started at: $(date)"
echo ""

# Ensure we're using real AWS (not LocalStack)
unset AWS_ENDPOINT_URL
unset LOCALSTACK_ENDPOINT_URL

# Ensure AWS_PROFILE is set
if [ -z "$AWS_PROFILE" ]; then
    export AWS_PROFILE=uic
    echo -e "${YELLOW}Setting AWS_PROFILE=uic${NC}"
fi

echo -e "${BLUE}Step 1: Verifying AWS Credentials${NC}"
echo "Profile: $AWS_PROFILE"

# Check if SSO session is valid
if ! aws sts get-caller-identity &>/dev/null; then
    echo -e "${YELLOW}SSO session expired. Logging in...${NC}"
    aws sso login --profile $AWS_PROFILE
fi

# Display current identity
echo -e "${GREEN}✓ AWS Identity:${NC}"
aws sts get-caller-identity
echo ""

# Get account info
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION="us-east-1"

echo -e "${BLUE}Step 2: Terraform Configuration${NC}"
cd terraform

# Check if terraform.tfvars exists
if [ ! -f terraform.tfvars ]; then
    echo -e "${YELLOW}Creating terraform.tfvars from example...${NC}"
    cp terraform.tfvars.example terraform.tfvars
    echo -e "${RED}⚠ Please edit terraform/terraform.tfvars with your email address${NC}"
    echo -e "${RED}Then run this script again.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ terraform.tfvars exists${NC}"
echo ""

echo -e "${BLUE}Step 3: Terraform Init${NC}"
terraform init
echo ""

echo -e "${BLUE}Step 4: Terraform Plan${NC}"
# Save plan to file for guaranteed execution
PLAN_FILE="tfplan-${TIMESTAMP}.tfplan"
echo "💾 Saving plan to: $PLAN_FILE"
terraform plan -out=$PLAN_FILE
echo ""

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Ready to apply the plan above?${NC}"
echo "Plan saved to: $PLAN_FILE"
echo -e "${YELLOW}========================================${NC}"
read -p "Continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo -e "${RED}Deployment cancelled.${NC}"
    echo -e "${YELLOW}Plan saved for later use: terraform/$PLAN_FILE${NC}"
    echo -e "${YELLOW}To apply later: cd terraform && terraform apply $PLAN_FILE${NC}"
    exit 0
fi

echo -e "${BLUE}Step 5: Terraform Apply${NC}"
echo "⏳ This will take 5-10 minutes..."
echo ""

# Apply the saved plan (guarantees exactly what was reviewed)
terraform apply $PLAN_FILE

# Clean up plan file after successful apply
rm -f $PLAN_FILE
echo -e "${GREEN}✓ Plan file cleaned up${NC}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Infrastructure Created Successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Save outputs
terraform output > ../outputs.txt
echo -e "${GREEN}✓ Outputs saved to outputs.txt${NC}"

# Get key outputs
ECR_REPO=$(terraform output -raw ecr_repository_url)
ECS_CLUSTER=$(terraform output -raw ecs_cluster_name)
ECS_SERVICE=$(terraform output -raw ecs_service_name)

echo ""
echo -e "${BLUE}Next Steps:${NC}"
echo "1. Build and push Docker image:"
echo -e "   ${GREEN}cd ..${NC}"
echo -e "   ${GREEN}./scripts/deploy-docker.sh${NC}"
echo ""
echo "Key Resources Created:"
echo "  ECR Repository: $ECR_REPO"
echo "  ECS Cluster: $ECS_CLUSTER"
echo "  ECS Service: $ECS_SERVICE"
echo ""
echo "Deployment completed at: $(date)"
echo "📝 Full log saved to: $LOG_FILE"
