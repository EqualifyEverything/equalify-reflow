# Equalify PDF Converter - Terraform Outputs

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = aws_lb.main.dns_name
}

output "alb_url" {
  description = "URL of the Application Load Balancer"
  value       = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.main.dns_name}"
}

output "application_url" {
  description = "Primary application URL (with HTTPS if domain configured)"
  value       = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.main.dns_name}"
}

output "domain_configuration" {
  description = "Domain configuration details"
  value = {
    production_domain = var.domain_name != "" ? var.domain_name : "Not configured"
    staging_domain    = var.staging_domain_name != "" ? var.staging_domain_name : "Not configured"
    alb_dns_name      = aws_lb.main.dns_name
    https_enabled     = var.domain_name != "" ? true : false
  }
}

output "ecr_repository_url" {
  description = "URL of the ECR repository"
  value       = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "Name of the ECS service"
  value       = aws_ecs_service.app.name
}

output "redis_endpoint" {
  description = "Redis cluster endpoint"
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "redis_port" {
  description = "Redis cluster port"
  value       = aws_elasticache_cluster.redis.cache_nodes[0].port
}

output "s3_temp_bucket" {
  description = "Name of S3 temp bucket"
  value       = aws_s3_bucket.temp.id
}

output "s3_results_bucket" {
  description = "Name of S3 results bucket"
  value       = aws_s3_bucket.results.id
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group name"
  value       = aws_cloudwatch_log_group.app.name
}

output "task_role_arn" {
  description = "ARN of the ECS task role"
  value       = aws_iam_role.ecs_task_role.arn
}

output "execution_role_arn" {
  description = "ARN of the ECS execution role"
  value       = aws_iam_role.ecs_execution_role.arn
}

# AI Provider Configuration
output "ai_provider" {
  description = "Configured AI provider (bedrock only)"
  value       = var.ai_provider
}

output "bedrock_model_id" {
  description = "Bedrock model ID (if using bedrock)"
  value       = var.ai_provider == "bedrock" ? var.bedrock_model_id : "N/A"
}

# Deployment information
output "deployment_info" {
  description = "Key information for deployment"
  value = {
    ecr_repository    = aws_ecr_repository.app.repository_url
    ecs_cluster       = aws_ecs_cluster.main.name
    ecs_service       = aws_ecs_service.app.name
    application_url   = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.main.dns_name}"
    alb_dns_name      = aws_lb.main.dns_name
    region            = var.aws_region
    ai_provider       = var.ai_provider
    bedrock_model     = var.ai_provider == "bedrock" ? var.bedrock_model_id : "N/A"
    https_enabled     = var.domain_name != "" ? true : false
    production_domain = var.domain_name != "" ? var.domain_name : "Not configured"
  }
}
