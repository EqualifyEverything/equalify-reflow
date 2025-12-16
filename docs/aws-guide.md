# AWS Deployment & Operations Guide

Complete guide for deploying and managing the Equalify PDF Converter on AWS.

**Deployment URL:** http://equalify-pdf-alb-633052607.us-east-1.elb.amazonaws.com

> **🔧 AWS Profile Configuration:**
> This guide uses `<your-profile>` and `your-profile-name` as placeholders.
> **Setup:** Configure your AWS SSO profile in `~/.aws/config` and set `AWS_PROFILE=your-profile-name` in `.env`
> **Usage:** All `make aws-*` commands and deployment scripts automatically use your `.env` profile.

---

## Table of Contents

1. [New Team Member Setup](#new-team-member-setup)
2. [Quick Start](#quick-start)
3. [Initial Deployment](#initial-deployment)
4. [Testing Your Deployment](#testing-your-deployment)
5. [Daily Operations](#daily-operations)
6. [Monitoring & Debugging](#monitoring--debugging)
7. [Cost Protection & Alerting](#cost-protection--alerting)
8. [Troubleshooting](#troubleshooting)

---

## New Team Member Setup

**If you're new and just need AWS access (5 minutes):**

### Step 1: Install AWS CLI
```bash
# macOS
brew install awscli

# Or download: https://aws.amazon.com/cli/
```

### Step 2: Configure AWS Profile
```bash
# 1. Copy example config and edit with YOUR actual AWS values
cat .aws-config-example >> ~/.aws/config
# Edit ~/.aws/config - replace YOUR-PROFILE-NAME, YOUR-SSO-START-URL, etc.

# 2. Set your profile in .env
echo "AWS_PROFILE=your-profile-name" >> .env

# 3. Login to AWS SSO
aws sso login --profile your-profile-name

# 4. Verify credentials work
aws sts get-caller-identity
```

**Note:** Replace `your-profile-name` with your actual profile name. All `make aws-*` commands and deployment scripts will use the `AWS_PROFILE` set in `.env`.

### Step 3: Test Access
```bash
make aws-health   # Check deployment health
make aws-status   # Show ECS service status
```

**Done!** You can now use all `make aws-*` commands.

**Auto-login:** All `make aws-*` commands automatically detect expired SSO tokens and prompt you to login. No need to manually check!

**Optional:** For `make aws-shell` to work, install the AWS Session Manager plugin:
```bash
brew install --cask session-manager-plugin  # macOS
```

---

## Quick Start

**For first-time deployment (if infrastructure doesn't exist yet):**
```bash
# 1. Configure AWS CLI and set AWS_PROFILE in .env (see above)
aws sso login --profile your-profile-name

# 2. Deploy infrastructure
./scripts/deploy-infrastructure.sh

# 3. Deploy application
./scripts/deploy-app.sh

# 4. Verify
make aws-health
```

**For daily operations:**
```bash
make aws-health   # Check deployment
make aws-logs     # View logs (auto-login if token expired)
make aws-status   # ECS status
make aws-shell    # Connect to ECS container (requires session-manager-plugin)
```

**Note:** All commands automatically handle expired SSO tokens - they'll prompt you to login and retry.

---

## Initial Deployment

### Prerequisites

- AWS CLI v2 installed
- Docker installed (for building images)
- Terraform installed
- AWS SSO configured for UIC account

### Step 1: AWS Authentication

```bash
# Configure AWS profile (one-time)
cat .aws-config-example >> ~/.aws/config

# Login to AWS
aws sso login --profile <your-profile>

# Verify credentials
aws sts get-caller-identity
```

### Step 2: Deploy Infrastructure

The deployment script handles Terraform automatically:

```bash
./scripts/deploy-infrastructure.sh
```

This creates:
- **VPC** with public/private subnets
- **ECS Cluster** (Fargate)
- **Application Load Balancer** (ALB)
- **ElastiCache** (Redis cluster)
- **S3 Buckets** (temp and results storage)
- **ECR Repository** (Docker images)
- **IAM Roles** (task execution and permissions)
- **CloudWatch** (log groups)

**Expected time:** 5-10 minutes

The script will:
1. Initialize Terraform
2. Show you a plan
3. Ask for confirmation
4. Apply the configuration

### Step 3: Deploy Application

Build and push Docker image to ECR:

```bash
./scripts/deploy-app.sh
```

This:
1. Builds Docker image for `linux/amd64` (ECS Fargate requirement)
2. Tags with timestamp and git SHA
3. Pushes to ECR
4. Updates ECS service (triggers deployment)
5. Waits for deployment to stabilize

**Expected time:** 5-8 minutes

### Step 4: Verify Deployment

```bash
# Quick health check
make aws-health

# Check ECS service
make aws-status

# View logs
make aws-logs
```

---

## Testing Your Deployment

### 1. Health Check (30 seconds)

```bash
# Using Makefile
make aws-health

# Or direct curl
curl http://equalify-pdf-alb-633052607.us-east-1.elb.amazonaws.com/health | jq
```

**Expected response:**
```json
{
  "status": "healthy",
  "checks": {
    "redis": true,
    "s3": true,
    "queue_depth": 0
  }
}
```

### 2. Submit Test Document

```bash
# Create a test PDF
python3 << 'EOF'
from reportlab.pdfgen import canvas
c = canvas.Canvas("/tmp/test.pdf")
c.drawString(100, 750, "Test Document for AWS")
c.save()
EOF

# Submit it
ALB_URL="http://equalify-pdf-alb-633052607.us-east-1.elb.amazonaws.com"
RESPONSE=$(curl -s -X POST $ALB_URL/api/documents/submit -F "file=@/tmp/test.pdf")
echo $RESPONSE | jq

# Save job ID
JOB_ID=$(echo $RESPONSE | jq -r '.job_id')
echo "Job ID: $JOB_ID"
```

### 3. Monitor Processing

```bash
# Poll status
for i in {1..30}; do
    STATUS=$(curl -s $ALB_URL/api/documents/$JOB_ID | jq -r '.status')
    echo "[$i] Status: $STATUS"
    [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ] && break
    sleep 2
done

# Get result
curl -s $ALB_URL/api/documents/$JOB_ID/result | jq
```

**Expected timeline:** ~10-30 seconds for completion

### 4. Verify Infrastructure

```bash
# Check ECS tasks
aws ecs describe-services \
    --cluster equalify-pdf-cluster \
    --services equalify-pdf-service \
    --region us-east-1 \
    --query 'services[0].{Desired:desiredCount,Running:runningCount,Status:status}'

# Check target health
make aws-status
```

---

## Daily Operations

### Quick Commands (Makefile)

```bash
make aws-health   # Check API health
make aws-logs     # Tail CloudWatch logs (Ctrl+C to exit)
make aws-status   # Show ECS service status
make aws-shell    # Connect to ECS container (requires session-manager-plugin)
make aws-deploy   # Full deployment (infrastructure + Docker)
```

### Manual AWS CLI Commands

All commands can use your configured profile from `.env`, or specify explicitly with `AWS_PROFILE=<your-profile>`:

```bash
# View logs (last 10 minutes)
AWS_PROFILE=<your-profile> aws logs tail /ecs/equalify-pdf --since 10m --region us-east-1

# View logs (follow/stream)
AWS_PROFILE=<your-profile> aws logs tail /ecs/equalify-pdf --follow --region us-east-1

# Filter for errors
AWS_PROFILE=<your-profile> aws logs tail /ecs/equalify-pdf --since 1h \
    --filter-pattern "ERROR" --region us-east-1

# Check task status
AWS_PROFILE=<your-profile> aws ecs list-tasks \
    --cluster equalify-pdf-cluster \
    --service-name equalify-pdf-service \
    --region us-east-1

# Describe specific task
TASK_ARN=$(AWS_PROFILE=<your-profile> aws ecs list-tasks \
    --cluster equalify-pdf-cluster \
    --service-name equalify-pdf-service \
    --region us-east-1 \
    --query 'taskArns[0]' \
    --output text)

AWS_PROFILE=<your-profile> aws ecs describe-tasks \
    --cluster equalify-pdf-cluster \
    --tasks $TASK_ARN \
    --region us-east-1
```

### Redeploying After Code Changes

```bash
# Just rebuild and redeploy Docker image
./scripts/deploy-app.sh

# Or manually
cd terraform
ECR_REPO=$(terraform output -raw ecr_repository_url)
cd ..

# Build
docker build --platform linux/amd64 -t equalify-pdf:latest .

# Login to ECR
AWS_PROFILE=<your-profile> aws ecr get-login-password --region us-east-1 | \
    docker login --username AWS --password-stdin $ECR_REPO

# Tag and push
docker tag equalify-pdf:latest $ECR_REPO:latest
docker push $ECR_REPO:latest

# Trigger ECS redeployment
AWS_PROFILE=<your-profile> aws ecs update-service \
    --cluster equalify-pdf-cluster \
    --service equalify-pdf-service \
    --force-new-deployment \
    --region us-east-1
```

---

## Monitoring & Debugging

### CloudWatch Logs

```bash
# Recent logs
make aws-logs

# Or with AWS CLI
AWS_PROFILE=<your-profile> aws logs tail /ecs/equalify-pdf --since 5m --region us-east-1

# Search for specific job
AWS_PROFILE=<your-profile> aws logs tail /ecs/equalify-pdf --since 1h \
    --filter-pattern "job_id=<JOB_ID>" --region us-east-1
```

### Prometheus Metrics

```bash
# Get metrics from API
curl -s http://equalify-pdf-alb-633052607.us-east-1.elb.amazonaws.com/metrics

# Filter specific metrics
curl -s http://equalify-pdf-alb-633052607.us-east-1.elb.amazonaws.com/metrics | grep http_requests_total
curl -s http://equalify-pdf-alb-633052607.us-east-1.elb.amazonaws.com/metrics | grep queue_depth
```

### ECS Task Health

```bash
# List running tasks
AWS_PROFILE=<your-profile> aws ecs list-tasks \
    --cluster equalify-pdf-cluster \
    --service-name equalify-pdf-service \
    --desired-status RUNNING \
    --region us-east-1

# Check task health
make aws-status
```

### S3 Bucket Contents

```bash
# List temp bucket
AWS_PROFILE=<your-profile> aws s3 ls s3://equalify-pdf-temp-380610849750/

# List results bucket
AWS_PROFILE=<your-profile> aws s3 ls s3://equalify-pdf-results-380610849750/

# Download specific file
AWS_PROFILE=<your-profile> aws s3 cp s3://equalify-pdf-results-380610849750/<key> ./downloaded.md
```

### Redis Monitoring

ElastiCache Redis metrics via CloudWatch:

```bash
# CPU utilization
AWS_PROFILE=<your-profile> aws cloudwatch get-metric-statistics \
    --namespace AWS/ElastiCache \
    --metric-name CPUUtilization \
    --dimensions Name=CacheClusterId,Value=equalify-pdf-redis \
    --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
    --period 300 \
    --statistics Average \
    --region us-east-1
```

---

## Cost Protection & Alerting

The deployment includes multi-layer protection against unexpected costs and abuse.

### Budget Alerts

AWS Budget alerts notify you when spending approaches or exceeds thresholds:

| Budget | Limit | Alerts At | Purpose |
|--------|-------|-----------|---------|
| Bedrock Daily | $50/day | 50%, 80%, 100% | AI processing cost control |
| Monthly Total | $500/month | 50%, 80%, 100% + forecast | Overall AWS spend limit |

**Configuration** (`terraform/terraform.tfvars`):
```hcl
alarm_email                = "your-email@example.com"
bedrock_daily_budget_limit = "50"   # Adjust based on expected usage
monthly_budget_limit       = "500"  # Adjust based on budget
```

### Rate Limiting

Application-level rate limiting (Redis-based sliding window):

| Endpoint | Limit | Window | Purpose |
|----------|-------|--------|---------|
| POST /api/documents/submit | 25/IP | 1 hour | Prevent individual abuse |
| GET /api/documents/*/status | 100/IP | 1 hour | Prevent polling storms |
| Global submissions | 1000 | 24 hours | System-wide cost control |

At ~$0.20/document, the 1000/day global limit caps Bedrock spend at ~$200/day.

### CloudWatch Alarms

Infrastructure alarms with SNS email notifications:

| Alarm | Threshold | Action |
|-------|-----------|--------|
| ECS High CPU | >80% for 10 min | Email alert |
| ECS High Memory | >85% for 10 min | Email alert |
| ALB 5xx Errors | >10 in 5 min | Email alert |
| Unhealthy Tasks | <1 healthy | Email alert |
| Bedrock Throttling | >10 errors in 5 min | Email alert |

### Authentication Layers

| Layer | Protection |
|-------|------------|
| API Key | Required for all API endpoints (constant-time comparison) |
| HTTP Basic Auth | Swagger UI / Demo UI access |
| CORS | Restricts cross-origin requests |

### Monitoring Cost Spend

```bash
# Check current month AWS costs
aws ce get-cost-and-usage \
    --time-period Start=$(date +%Y-%m-01),End=$(date +%Y-%m-%d) \
    --granularity MONTHLY \
    --metrics BlendedCost \
    --group-by Type=DIMENSION,Key=SERVICE \
    --region us-east-1

# Check Bedrock-specific costs
aws ce get-cost-and-usage \
    --time-period Start=$(date +%Y-%m-01),End=$(date +%Y-%m-%d) \
    --granularity DAILY \
    --metrics BlendedCost \
    --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Bedrock"]}}' \
    --region us-east-1
```

### Adjusting Limits

**Rate limits** - Edit `src/services/rate_limit_service.py`:
```python
self.SUBMIT_PER_IP_LIMIT = 25      # Per-IP submissions/hour
self.GLOBAL_SUBMIT_LIMIT = 1000    # Global submissions/day
```

**Budget alerts** - Edit `terraform/terraform.tfvars` and run `terraform apply`:
```hcl
bedrock_daily_budget_limit = "100"  # Increase daily limit
monthly_budget_limit       = "1000" # Increase monthly limit
```

**ECS scaling ceiling** - Edit `terraform/terraform.tfvars`:
```hcl
ecs_max_capacity = 5  # Maximum concurrent tasks
```

---

## Troubleshooting

### Issue: Tasks Not Starting

**Symptoms:** `RunningCount = 0`, service events show task failures

**Debug:**
```bash
# Check service events
AWS_PROFILE=<your-profile> aws ecs describe-services \
    --cluster equalify-pdf-cluster \
    --services equalify-pdf-service \
    --region us-east-1 \
    --query 'services[0].events[:5]'

# Check stopped tasks
AWS_PROFILE=<your-profile> aws ecs list-tasks \
    --cluster equalify-pdf-cluster \
    --desired-status STOPPED \
    --region us-east-1

# Get stopped task details
STOPPED_TASK=$(AWS_PROFILE=<your-profile> aws ecs list-tasks \
    --cluster equalify-pdf-cluster \
    --desired-status STOPPED \
    --region us-east-1 \
    --query 'taskArns[0]' \
    --output text)

AWS_PROFILE=<your-profile> aws ecs describe-tasks \
    --cluster equalify-pdf-cluster \
    --tasks $STOPPED_TASK \
    --region us-east-1 \
    --query 'tasks[0].{StoppedReason:stoppedReason,Containers:containers[*].reason}'
```

**Common causes:**
- Invalid environment variables in task definition
- ECR image not found or permissions issue
- Insufficient CPU/memory allocation

### Issue: Health Check Failing

**Symptoms:** ALB shows targets as "unhealthy"

**Debug:**
```bash
# Check target health
TARGET_GROUP_ARN=$(AWS_PROFILE=<your-profile> aws elbv2 describe-target-groups \
    --region us-east-1 \
    --query "TargetGroups[?TargetGroupName=='equalify-pdf-tg'].TargetGroupArn" \
    --output text)

AWS_PROFILE=<your-profile> aws elbv2 describe-target-health \
    --target-group-arn $TARGET_GROUP_ARN \
    --region us-east-1

# Check application logs for errors
AWS_PROFILE=<your-profile> aws logs tail /ecs/equalify-pdf --since 5m --region us-east-1 | grep ERROR
```

**Common causes:**
- Redis connection failure
- S3 permissions issue
- Application crash on startup

### Issue: Redis Connection Errors

**Symptoms:** Logs show "Connection refused" or "timeout" to Redis

**Debug:**
```bash
# Verify Redis cluster status
AWS_PROFILE=<your-profile> aws elasticache describe-cache-clusters \
    --cache-cluster-id equalify-pdf-redis \
    --show-cache-node-info \
    --region us-east-1

# Check security group allows ECS -> Redis
# Redis security group must allow inbound on 6379 from ECS security group
```

### Issue: S3 Access Denied

**Symptoms:** Logs show "Access Denied" for S3 operations

**Debug:**
```bash
# Check task role permissions
AWS_PROFILE=<your-profile> aws iam get-role-policy \
    --role-name equalify-pdf-ecs-task-role \
    --policy-name S3Access \
    --region us-east-1

# Test S3 access from running task (if exec enabled)
make aws-shell
# Inside container:
aws s3 ls s3://equalify-pdf-temp-380610849750/
```

### Issue: Slow Processing

**Symptoms:** Jobs taking >2 minutes to complete

**Debug:**
```bash
# Check CPU/Memory usage
AWS_PROFILE=<your-profile> aws ecs describe-tasks \
    --cluster equalify-pdf-cluster \
    --tasks $(AWS_PROFILE=<your-profile> aws ecs list-tasks \
        --cluster equalify-pdf-cluster \
        --service-name equalify-pdf-service \
        --region us-east-1 \
        --query 'taskArns[0]' \
        --output text) \
    --region us-east-1 \
    --query 'tasks[0].containers[0].{CPU:cpu,Memory:memory}'

# Check for Bedrock throttling
AWS_PROFILE=<your-profile> aws logs tail /ecs/equalify-pdf --since 10m \
    --filter-pattern "throttle" --region us-east-1

# Monitor queue depth
curl -s http://equalify-pdf-alb-633052607.us-east-1.elb.amazonaws.com/metrics | grep queue_depth
```

---

## Infrastructure Details

**Deployed Resources:**
- **VPC:** vpc-07275602e9211f0af
- **ECS Cluster:** equalify-pdf-cluster
- **ECS Service:** equalify-pdf-service (1 task, Fargate)
- **ALB:** equalify-pdf-alb-633052607.us-east-1.elb.amazonaws.com
- **Redis:** equalify-pdf-redis.njtamw.0001.use1.cache.amazonaws.com:6379
- **S3 Temp:** equalify-pdf-temp-380610849750
- **S3 Results:** equalify-pdf-results-380610849750
- **ECR:** 380610849750.dkr.ecr.us-east-1.amazonaws.com/equalify-pdf
- **Region:** us-east-1
- **AI Provider:** AWS Bedrock (Claude 3.5 Haiku)

---

## Additional Resources

- **AWS Console:** https://us-east-1.console.aws.amazon.com/ecs/v2/clusters/equalify-pdf-cluster
- **CloudWatch Logs:** https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups/log-group//ecs/equalify-pdf
- **Terraform State:** `terraform/terraform.tfstate` (local)

---

**Last Updated:** 2025-12-16
