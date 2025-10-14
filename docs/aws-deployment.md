# AWS Deployment Guide

This guide walks you through deploying the Equalify PDF Converter to AWS using Terraform and ECS Fargate.

## Important: Region Configuration

- **SSO Region:** `us-east-2` (AWS Identity Center - authentication only)
- **Resource Region:** `us-east-1` (All infrastructure: ECS, S3, Redis, ALB)

All deployment commands use `us-east-1` for resources, regardless of SSO region.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Infrastructure Setup](#infrastructure-setup)
- [First-Time Deployment](#first-time-deployment)
- [Subsequent Deployments](#subsequent-deployments)
- [Monitoring & Troubleshooting](#monitoring--troubleshooting)
- [Cost Management](#cost-management)
- [Security](#security)

---

## Prerequisites

### Required Tools

1. **AWS CLI** (v2.x)
   ```bash
   aws --version
   # Install: https://aws.amazon.com/cli/
   ```

2. **Terraform** (v1.0+)
   ```bash
   terraform --version
   # Install: https://www.terraform.io/downloads
   ```

3. **Docker** (v20.10+)
   ```bash
   docker --version
   ```

4. **Git**
   ```bash
   git --version
   ```

### AWS Account Setup

#### UIC Team Members (AWS SSO)

1. **Configure AWS SSO**
   ```bash
   aws configure sso

   # Enter these values:
   SSO session name: uic
   SSO start URL: https://d-9a672cc795.awsapps.com/start
   SSO region: us-east-2
   SSO registration scopes: [press Enter]

   # Browser will open - authorize the request
   # Select the Equalify AWS account and AWSAdministratorAccess role

   # Then configure CLI defaults:
   CLI default region: us-east-1
   CLI output format: json
   CLI profile name: uic
   ```

2. **Set AWS Profile**
   ```bash
   export AWS_PROFILE=uic

   # Make it permanent (add to ~/.zshrc or ~/.bashrc):
   echo 'export AWS_PROFILE=uic' >> ~/.zshrc
   ```

3. **Verify AWS Access**
   ```bash
   aws sts get-caller-identity
   # Should show: Your user ARN and the Equalify account information
   ```

**Note:** SSO authenticates in `us-east-2` (Identity Center region), but all infrastructure deploys to `us-east-1` (resource region).

#### External Collaborators (IAM User)

If IT created an IAM user for you:

```bash
aws configure
# AWS Access Key ID: [Your access key]
# AWS Secret Access Key: [Your secret key]
# Default region: us-east-1
# Default output format: json
```

---

## Infrastructure Setup

### Step 1: Configure Terraform Variables

1. **Copy the example variables file:**
   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars
   ```

2. **Edit `terraform.tfvars`:**
   ```hcl
   # AWS Configuration
   aws_region  = "us-east-1"
   environment = "production"

   # ECS Configuration
   ecs_task_cpu      = 2048  # 2 vCPU
   ecs_task_memory   = 4096  # 4GB
   ecs_desired_count = 2     # Number of tasks

   # Monitoring
   alarm_email = "disaac4@uic.edu"

   # AI Provider Configuration
   ai_provider = "bedrock"  # Using AWS Bedrock (no API key needed)
   bedrock_model_id = "anthropic.claude-3-5-haiku-20241022"
   enable_bedrock_metrics = true

   # Additional tags
   additional_tags = {
     Department = "UIC-DASE"
     ManagedBy  = "Dylan Isaac"
     CostCenter = "Accessibility"
   }
   ```

   **Note:** This configuration uses AWS Bedrock with IAM-based authentication. No API keys required!

### Step 2: Initialize Terraform

```bash
cd terraform
terraform init
```

This will:
- Download required providers (AWS)
- Initialize backend (local by default)

### Step 3: Review Infrastructure Plan

```bash
terraform plan
```

Review the plan to see what resources will be created:
- VPC with public/private subnets
- Security groups
- Application Load Balancer
- ECS Cluster and Service
- ElastiCache Redis
- S3 Buckets
- ECR Repository
- IAM Roles
- CloudWatch Log Groups

### Step 4: Create Infrastructure

```bash
terraform apply
```

Type `yes` when prompted.

**This will take 5-10 minutes.** Terraform will create all AWS resources.

### Step 5: Save Outputs

```bash
terraform output > outputs.txt
```

Save these values - you'll need them for deployment:
- ECR Repository URL
- ALB URL
- ECS Cluster Name
- Redis Endpoint

---

## First-Time Deployment

### Step 1: Build and Push Initial Image

Since the ECS service references an image that doesn't exist yet, we need to build and push it:

```bash
# From project root
cd ..

# Get ECR repository URL
ECR_REPO=$(cd terraform && terraform output -raw ecr_repository_url)
AWS_REGION="us-east-1"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Login to ECR
aws ecr get-login-password --region ${AWS_REGION} | \
    docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

# Build for linux/amd64 (required for ECS)
docker build --platform linux/amd64 -t equalify-pdf:latest .

# Tag and push
docker tag equalify-pdf:latest ${ECR_REPO}:latest
docker push ${ECR_REPO}:latest
```

### Step 2: Trigger First Deployment

```bash
# Get ECS details
cd terraform
ECS_CLUSTER=$(terraform output -raw ecs_cluster_name)
ECS_SERVICE=$(terraform output -raw ecs_service_name)
cd ..

# Force new deployment
aws ecs update-service \
    --cluster ${ECS_CLUSTER} \
    --service ${ECS_SERVICE} \
    --force-new-deployment \
    --region us-east-1

# Wait for deployment to complete (2-5 minutes)
aws ecs wait services-stable \
    --cluster ${ECS_CLUSTER} \
    --services ${ECS_SERVICE} \
    --region us-east-1
```

### Step 3: Verify Deployment

```bash
# Get ALB URL
cd terraform
ALB_URL=$(terraform output -raw alb_url)
cd ..

# Test health endpoint
curl ${ALB_URL}/health

# Should return: {"status":"healthy","checks":{...}}
```

### Step 4: View API Documentation

Open in browser:
```
http://<ALB_URL>/docs
```

---

## Subsequent Deployments

For all future deployments, use the deployment script:

### Quick Deployment

```bash
# Deploy from main branch
./scripts/deploy.sh production main
```

### Deploy from Specific Branch/Tag

```bash
# Deploy from feature branch
./scripts/deploy.sh staging feature-branch

# Deploy from git tag
./scripts/deploy.sh production v1.2.3
```

### What the Script Does

1. Checks out specified git reference
2. Runs tests (`make test-fast`)
3. Builds Docker image
4. Tags with git SHA + timestamp
5. Pushes to ECR
6. Updates ECS service
7. Waits for deployment to complete

### Manual Deployment (Without Script)

If you prefer manual control:

```bash
# 1. Build image
docker build --platform linux/amd64 -t equalify-pdf:latest .

# 2. Login to ECR
aws ecr get-login-password --region us-east-1 | \
    docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# 3. Tag and push
ECR_REPO=$(cd terraform && terraform output -raw ecr_repository_url)
docker tag equalify-pdf:latest ${ECR_REPO}:latest
docker push ${ECR_REPO}:latest

# 4. Update ECS service
ECS_CLUSTER=$(cd terraform && terraform output -raw ecs_cluster_name)
ECS_SERVICE=$(cd terraform && terraform output -raw ecs_service_name)
aws ecs update-service \
    --cluster ${ECS_CLUSTER} \
    --service ${ECS_SERVICE} \
    --force-new-deployment
```

---

## Monitoring & Troubleshooting

### View Application Logs

```bash
# Real-time logs
aws logs tail /ecs/equalify-pdf --follow

# Last 1 hour
aws logs tail /ecs/equalify-pdf --since 1h

# Filter by error
aws logs tail /ecs/equalify-pdf --filter-pattern "ERROR"
```

### Check ECS Service Status

```bash
ECS_CLUSTER=$(cd terraform && terraform output -raw ecs_cluster_name)
ECS_SERVICE=$(cd terraform && terraform output -raw ecs_service_name)

aws ecs describe-services \
    --cluster ${ECS_CLUSTER} \
    --services ${ECS_SERVICE} \
    --query 'services[0].[serviceName,status,runningCount,desiredCount]'
```

### Check Task Status

```bash
# List running tasks
aws ecs list-tasks --cluster ${ECS_CLUSTER} --service-name ${ECS_SERVICE}

# Describe specific task
TASK_ARN=$(aws ecs list-tasks --cluster ${ECS_CLUSTER} --service-name ${ECS_SERVICE} --query 'taskArns[0]' --output text)
aws ecs describe-tasks --cluster ${ECS_CLUSTER} --tasks ${TASK_ARN}
```

### View CloudWatch Alarms

```bash
aws cloudwatch describe-alarms --alarm-name-prefix "equalify-pdf"
```

### Connect to Redis (via ECS Exec)

```bash
# Enable ECS Exec on service (if not already enabled)
aws ecs update-service \
    --cluster ${ECS_CLUSTER} \
    --service ${ECS_SERVICE} \
    --enable-execute-command

# Get task ARN
TASK_ARN=$(aws ecs list-tasks --cluster ${ECS_CLUSTER} --service-name ${ECS_SERVICE} --query 'taskArns[0]' --output text | awk -F/ '{print $NF}')

# Connect to task
aws ecs execute-command \
    --cluster ${ECS_CLUSTER} \
    --task ${TASK_ARN} \
    --container api-gateway \
    --interactive \
    --command "/bin/bash"

# Inside container, connect to Redis
redis-cli -h <redis-endpoint>
```

### Common Issues

**Issue: Tasks failing health checks**
```bash
# Check task logs
aws logs tail /ecs/equalify-pdf --since 10m

# Check security group allows ALB -> ECS on port 8080
aws ec2 describe-security-groups --group-ids <ecs-sg-id>
```

**Issue: Can't connect to Redis**
```bash
# Verify Redis endpoint
cd terraform && terraform output redis_endpoint

# Check security group allows ECS -> Redis on port 6379
```

**Issue: S3 access denied**
```bash
# Verify task role has S3 permissions
aws iam get-role-policy \
    --role-name equalify-pdf-ecs-task-role \
    --policy-name equalify-pdf-ecs-task-s3
```

---

## Cost Management

### Monthly Cost Breakdown (Estimated)

| Resource | Configuration | Monthly Cost |
|----------|---------------|--------------|
| ECS Fargate (2 tasks) | 2 vCPU, 4GB RAM | ~$60 |
| ElastiCache Redis | cache.t4g.small | ~$25 |
| Application Load Balancer | - | ~$20 |
| S3 Storage | 10GB + requests | ~$5 |
| Data Transfer | 50GB/month | ~$5 |
| CloudWatch Logs | 5GB/month | ~$2.50 |
| **Total Infrastructure** | | **~$117.50** |
| AWS Bedrock (Claude 3.5 Haiku) | ~$0.15-0.25/document | Variable |

**Note:** Bedrock costs include:
- Input tokens: Document text + image analysis of each page
- Output tokens: Reasoning + corrected accessible text
- Typical 20-page PDF: ~$0.20/document
- Cost scales with document length and page count

### Cost Optimization Tips

1. **Use Savings Plans** for predictable workloads (up to 50% discount)
2. **Single NAT Gateway** for dev/staging (already configured)
3. **Reduce ECS task count** during off-hours
4. **Enable S3 Intelligent-Tiering** for long-term storage
5. **Use Reserved Redis nodes** for production (40% discount)

### View Current Costs

```bash
# AWS Cost Explorer (requires CLI setup)
aws ce get-cost-and-usage \
    --time-period Start=2024-01-01,End=2024-01-31 \
    --granularity MONTHLY \
    --metrics BlendedCost \
    --group-by Type=SERVICE
```

---

## Security

### Secrets Management

**Using AWS Bedrock (Current Setup):**
- ✅ No API keys or secrets required
- ✅ Authentication via IAM roles (more secure)
- ✅ Task role has Bedrock permissions for Claude models

**If using Anthropic API instead:**
```bash
# Update secret in AWS Secrets Manager
aws secretsmanager update-secret \
    --secret-id equalify-pdf-anthropic-api-key \
    --secret-string "sk-ant-new-api-key"

# Rotate secret (requires ECS service restart)
aws ecs update-service --cluster ${ECS_CLUSTER} --service ${ECS_SERVICE} --force-new-deployment
```

### IAM Best Practices

- ✅ Task role has minimal S3/CloudWatch/Bedrock permissions
- ✅ Execution role can only pull images and read secrets
- ✅ No hardcoded credentials in code
- ✅ IAM-based authentication for all AWS services

### Network Security

- ✅ ECS tasks in private subnets (no direct internet access)
- ✅ ALB in public subnets only
- ✅ Security groups restrict traffic to necessary ports
- ✅ S3 buckets have encryption enabled

### Enable HTTPS (Optional)

1. **Request ACM Certificate**:
   ```bash
   aws acm request-certificate \
       --domain-name pdf-converter.uic.edu \
       --validation-method DNS
   ```

2. **Update Terraform**:
   ```hcl
   # In terraform/alb.tf, uncomment HTTPS listener
   ```

3. **Apply changes**:
   ```bash
   cd terraform && terraform apply
   ```

---

## Infrastructure Teardown

To delete all AWS resources:

```bash
cd terraform
terraform destroy
```

**Warning**: This will delete:
- All ECS tasks and services
- Load balancers
- S3 buckets (if empty)
- Redis cluster
- All data

---

## Next Steps

1. **Set up CI/CD** - Use GitHub Actions to automate deployments
2. **Configure Custom Domain** - Point your domain to ALB
3. **Enable HTTPS** - Add ACM certificate and HTTPS listener
4. **Set up Monitoring** - Create CloudWatch dashboards
5. **Configure Backups** - Enable Redis snapshots, S3 versioning

---

## Support

**Issues with AWS deployment?**

1. Check [Troubleshooting](#monitoring--troubleshooting) section
2. View AWS service health: https://status.aws.amazon.com/
3. Review CloudWatch logs: `aws logs tail /ecs/equalify-pdf --follow`
4. Create GitHub issue with error details

---

**Last Updated**: 2025-10-07
**Terraform Version**: 1.0+
**AWS Provider Version**: 5.0+
**AI Provider**: AWS Bedrock (Claude 3.5 Haiku)
