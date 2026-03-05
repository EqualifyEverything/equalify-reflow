# Equalify PDF Converter - Infrastructure Configuration
# Non-secret values — tracked in version control

# AWS Configuration
aws_region  = "us-east-1"
environment = "production"

# AWS Profile (using environment variable AWS_PROFILE=uic)
aws_profile = "" # Leave empty to use AWS_PROFILE env var

# ECS Configuration - 2 vCPU for api-gateway + docling-serve sidecar
ecs_task_cpu      = 2048 # 2 vCPU (docling-serve needs ~1 vCPU for model inference)
ecs_task_memory   = 8192 # 8GB (docling-serve needs ~4GB for models)
ecs_desired_count = 1
ecs_min_capacity  = 1
ecs_max_capacity  = 5

# Redis Configuration
redis_node_type       = "cache.t4g.micro" # $12/month (down from t4g.small $25/mo)
redis_num_cache_nodes = 1

# S3 Configuration
s3_temp_lifecycle_days = 7

# Domain Configuration (HTTPS)
domain_name = "pdf.equalify.uic.edu"
# staging_domain_name = "staging-pdf.equalify.uic.edu"  # Uncomment later for staging
create_route53_record = false # Using UIC DNS, not Route 53

# Monitoring
enable_cloudwatch_alarms = true
alarm_email              = "disaac4@uic.edu"

# Budget Alerts (cost protection)
bedrock_daily_budget_limit = "50"  # $50/day Bedrock limit (alerts at $25, $40, $50)
monthly_budget_limit       = "150" # $150/month for us-east-1 (reduced from $500 after right-sizing)

# AI Provider Configuration
ai_provider = "bedrock" # Using AWS Bedrock (no API key needed)

# Bedrock Configuration
bedrock_model_id       = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
enable_bedrock_metrics = true

# Authentication (non-secret)
docs_username = "uic-admin"

# Additional Tags
additional_tags = {
  Department = "UIC-DASE"
  ManagedBy  = "Dylan Isaac"
  CostCenter = "Accessibility"
}
