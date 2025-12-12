# Equalify PDF Converter - Terraform Variables

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use (leave empty to use default credentials)"
  type        = string
  default     = "" # Set via AWS_PROFILE environment variable or terraform.tfvars
}

variable "environment" {
  description = "Environment name (dev, staging, production)"
  type        = string
  default     = "production"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "equalify-pdf"
}

# VPC Configuration
variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones_count" {
  description = "Number of availability zones to use"
  type        = number
  default     = 2
}

# ECS Configuration
variable "ecs_task_cpu" {
  description = "CPU units for ECS task (1024 = 1 vCPU)"
  type        = number
  default     = 2048 # 2 vCPU
}

variable "ecs_task_memory" {
  description = "Memory for ECS task in MB"
  type        = number
  default     = 4096 # 4GB
}

variable "ecs_desired_count" {
  description = "Desired number of ECS tasks"
  type        = number
  default     = 2
}

variable "ecs_min_capacity" {
  description = "Minimum number of ECS tasks for auto-scaling"
  type        = number
  default     = 1
}

variable "ecs_max_capacity" {
  description = "Maximum number of ECS tasks for auto-scaling"
  type        = number
  default     = 5
}

# Redis Configuration
variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
  default     = "cache.t4g.small" # 0.5 vCPU, 1.37GB RAM
}

variable "redis_num_cache_nodes" {
  description = "Number of Redis cache nodes"
  type        = number
  default     = 1
}

# S3 Configuration
variable "s3_temp_lifecycle_days" {
  description = "Days before temp files are deleted"
  type        = number
  default     = 7
}

# Application Configuration
variable "container_port" {
  description = "Port exposed by the Docker container"
  type        = number
  default     = 8080
}

variable "health_check_path" {
  description = "Health check endpoint path"
  type        = string
  default     = "/health"
}

# Domain Configuration (optional)
variable "domain_name" {
  description = "Production domain name for ALB (e.g., pdf.equalify.uic.edu). Leave empty for default AWS domain"
  type        = string
  default     = ""
}

variable "staging_domain_name" {
  description = "Staging domain name for ALB (e.g., staging-pdf.equalify.uic.edu). Leave empty to skip"
  type        = string
  default     = ""
}

variable "create_route53_record" {
  description = "Whether to create Route53 DNS record (only if using Route 53 for DNS)"
  type        = bool
  default     = false
}

# Monitoring
variable "enable_cloudwatch_alarms" {
  description = "Enable CloudWatch alarms for monitoring"
  type        = bool
  default     = true
}

variable "alarm_email" {
  description = "Email address for CloudWatch alarm notifications"
  type        = string
  default     = ""
}

# AI Provider Configuration
variable "ai_provider" {
  description = "AI provider to use (only 'bedrock' is supported)"
  type        = string
  default     = "bedrock"
  validation {
    condition     = var.ai_provider == "bedrock"
    error_message = "AI provider must be 'bedrock'. Only AWS Bedrock is supported."
  }
}

variable "bedrock_model_id" {
  description = "AWS Bedrock model ID for CloudWatch metrics (not used for model selection - see src/agents/model_tiers.py)"
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "enable_bedrock_metrics" {
  description = "Enable CloudWatch metrics for Bedrock usage"
  type        = bool
  default     = true
}

# Authentication Configuration
variable "api_keys" {
  description = "Comma-separated list of valid API keys for authentication (stored in Secrets Manager)"
  type        = string
  sensitive   = true
  # Set via terraform.tfvars or TF_VAR_api_keys environment variable
}

variable "docs_username" {
  description = "Username for Swagger/OpenAPI documentation HTTP Basic auth"
  type        = string
  default     = "admin"
}

variable "docs_password" {
  description = "Password for Swagger/OpenAPI documentation HTTP Basic auth (stored in Secrets Manager)"
  type        = string
  sensitive   = true
  # Set via terraform.tfvars or TF_VAR_docs_password environment variable
}

# Tags
variable "additional_tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}
