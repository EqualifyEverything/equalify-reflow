# Terraform Infrastructure for Equalify PDF Converter

This directory contains Terraform configuration for deploying the Equalify PDF Converter to AWS ECS Fargate.

## Quick Start

```bash
# 1. Copy variables file
cp terraform.tfvars.example terraform.tfvars

# 2. Edit terraform.tfvars with your values

# 3. Initialize Terraform
terraform init

# 4. Review plan
terraform plan

# 5. Apply infrastructure
terraform apply
```

**Note:** By default, the infrastructure uses AWS Bedrock (no API key required - uses IAM roles). If you want to use the Anthropic API instead, set:
```bash
export TF_VAR_ai_provider="anthropic"
export TF_VAR_anthropic_api_key="sk-ant-your-key"
```

## Files

- `main.tf` - Provider configuration and data sources
- `variables.tf` - Input variables
- `outputs.tf` - Output values
- `vpc.tf` - VPC and networking
- `security_groups.tf` - Security group rules
- `s3.tf` - S3 buckets for storage
- `ecr.tf` - ECR repository for Docker images
- `redis.tf` - ElastiCache Redis cluster
- `iam.tf` - IAM roles and policies
- `secrets.tf` - AWS Secrets Manager (for Anthropic API key if needed)
- `bedrock.tf` - AWS Bedrock IAM permissions and monitoring
- `alb.tf` - Application Load Balancer
- `ecs.tf` - ECS cluster, task, and service
- `cloudwatch.tf` - CloudWatch dashboard and alarms

## Architecture

```
Internet → ALB (Public Subnets)
           ↓
         ECS Fargate (Private Subnets)
           ↓
       Redis + S3 (Private)
```

## Resources Created

### Networking
- VPC (10.0.0.0/16)
- 2 Public subnets (for ALB)
- 2 Private subnets (for ECS, Redis)
- Internet Gateway
- NAT Gateway
- Route tables
- Security Groups

### Compute
- ECS Cluster
- ECS Task Definition (2 vCPU, 4GB RAM)
- ECS Service (Fargate)
- Auto-scaling policies (CPU/Memory based)

### Storage
- S3 temp bucket (7-day lifecycle)
- S3 results bucket (versioned, public read)
- ECR repository

### Database
- ElastiCache Redis cluster (cache.t4g.small)

### Load Balancing
- Application Load Balancer
- Target Group
- HTTP Listener (port 80)

### Security
- IAM execution role (ECR, Secrets Manager)
- IAM task role (S3, CloudWatch, Bedrock)
- Secrets Manager (Anthropic API key - optional, only for Anthropic provider)

### Monitoring
- CloudWatch Log Group
- CloudWatch Dashboard (Bedrock metrics, token usage, cost estimation)
- CloudWatch Alarms (CPU, Memory, 5xx errors, Bedrock throttling)
- SNS Topic for alerts (optional)

## Variables

### AI Provider Configuration

**Default:** AWS Bedrock (no API key required, uses IAM roles)

- `ai_provider` - AI provider to use: "bedrock" or "anthropic" (default: "bedrock")
- `bedrock_model_id` - Bedrock model ID (default: "anthropic.claude-3-haiku-20240307-v1:0")

**Only required if using Anthropic API:**
- `anthropic_api_key` - Set via environment variable: `export TF_VAR_anthropic_api_key="sk-ant-your-key"`

### Infrastructure Configuration

- `aws_region` - AWS region (default: us-east-1)
- `environment` - Environment name (default: production)
- `ecs_task_cpu` - CPU units (default: 2048)
- `ecs_task_memory` - Memory in MB (default: 4096)
- `ecs_desired_count` - Number of tasks (default: 2)
- `redis_node_type` - Redis instance type (default: cache.t4g.small)
- `alarm_email` - Email for CloudWatch alarms

See `variables.tf` for complete list.

## Outputs

After `terraform apply`, get outputs:

```bash
terraform output
```

Key outputs:
- `alb_url` - Application URL
- `ecr_repository_url` - Docker repository
- `ecs_cluster_name` - ECS cluster
- `redis_endpoint` - Redis connection endpoint

## Cost Estimate

Approximate monthly costs:
- ECS Fargate: ~$60 (2 tasks, 2 vCPU, 4GB)
- ElastiCache: ~$25 (cache.t4g.small)
- ALB: ~$20
- S3: ~$5
- CloudWatch: ~$2.50
- NAT Gateway: ~$32 (production), ~$16 (dev)

**Total: ~$117.50/month** (+ API costs)

## State Management

### Local State (Default)
State is stored locally in `terraform.tfstate`.

**Warning**: Do not commit this file to git!

### Remote State (Recommended for Teams)

Uncomment backend configuration in `main.tf`:

```hcl
backend "s3" {
  bucket         = "equalify-terraform-state"
  key            = "pdf-converter/terraform.tfstate"
  region         = "us-east-1"
  encrypt        = true
  dynamodb_table = "terraform-state-lock"
}
```

Create state bucket:
```bash
aws s3 mb s3://equalify-terraform-state
aws dynamodb create-table \
    --table-name terraform-state-lock \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --provisioned-throughput ReadCapacityUnits=1,WriteCapacityUnits=1
```

## Terraform Commands

```bash
# Initialize
terraform init

# Format code
terraform fmt

# Validate configuration
terraform validate

# Plan changes
terraform plan

# Apply changes
terraform apply

# Show outputs
terraform output

# Destroy infrastructure
terraform destroy

# Show current state
terraform show

# List resources
terraform state list

# Import existing resource
terraform import aws_s3_bucket.temp equalify-pdf-temp
```

## Environment-Specific Configurations

### Development
```hcl
environment         = "development"
ecs_desired_count   = 1
ecs_min_capacity    = 1
ecs_max_capacity    = 2
redis_node_type     = "cache.t4g.micro"
```

### Production
```hcl
environment         = "production"
ecs_desired_count   = 2
ecs_min_capacity    = 1
ecs_max_capacity    = 5
redis_node_type     = "cache.t4g.small"
enable_cloudwatch_alarms = true
```

## Troubleshooting

### Error: State lock
```bash
# Force unlock (use with caution)
terraform force-unlock <lock-id>
```

### Error: Resource already exists
```bash
# Import existing resource
terraform import <resource_type>.<name> <resource_id>
```

### Error: Invalid credentials
```bash
# Verify AWS credentials
aws sts get-caller-identity

# Reconfigure
aws configure
```

## Security Notes

- Never commit `terraform.tfvars` to git
- Never commit `.tfstate` files to git
- Use environment variables for sensitive values
- Enable MFA for AWS account
- Use IAM roles instead of access keys when possible
- Regularly rotate secrets
- Enable CloudTrail for audit logging

## Maintenance

### Updating Terraform
```bash
# Update providers
terraform init -upgrade

# Update modules
terraform get -update
```

### Updating Infrastructure
```bash
# Review changes
terraform plan

# Apply updates
terraform apply
```

### Backup State
```bash
# Backup state file
cp terraform.tfstate terraform.tfstate.backup
```

## References

- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [AWS ECS Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/)
- [Project Documentation](../docs/aws-deployment.md)

---

**Last Updated**: 2025-10-06
