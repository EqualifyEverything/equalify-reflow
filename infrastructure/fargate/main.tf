# ---------------------------------------------------------------------------
# Equalify Reflow - production Fargate deployment
# ---------------------------------------------------------------------------
# Goal of this stack:
#   * 2x redundant ECS tasks behind an ALB (no single point of failure)
#   * Independent autoscaling for the api-gateway and the workers
#   * Health checks pinned to the new /health/live + /health/ready endpoints
#   * Tenant-isolated by setting CANVAS_TENANT per service (one stack per
#     campus, sharing nothing with other tenants beyond AWS account quotas)
#
# Notes for operators:
#   * Reflow's stateful tier (Redis, S3) is NOT defined here - those live in
#     a separate stack so they outlive any Fargate redeploy. Inject their
#     endpoints via the *_endpoint variables.
#   * Container image must be published to ECR before applying. CI/CD docs
#     in ../../docs/explanation/production-readiness.md.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Inputs - everything campus-specific lives here. No defaults that bake one
# campus's identity into the module.
# ---------------------------------------------------------------------------
variable "tenant" {
  description = "Tenant id passed to the app as CANVAS_TENANT. One per campus."
  type        = string
}
variable "aws_region" {
  description = "AWS region for the ECS cluster, ALB, and supporting resources."
  type        = string
  default     = "us-east-1"
}
variable "vpc_id" {
  description = "Existing VPC to attach the ALB and tasks to."
  type        = string
}
variable "private_subnet_ids" {
  description = "Private subnets for Fargate tasks (multi-AZ; minimum 2)."
  type        = list(string)
  validation {
    condition     = length(var.private_subnet_ids) >= 2
    error_message = "Provide at least 2 private subnets across distinct AZs for HA."
  }
}
variable "public_subnet_ids" {
  description = "Public subnets for the ALB (multi-AZ; minimum 2)."
  type        = list(string)
  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "Provide at least 2 public subnets across distinct AZs for HA."
  }
}
variable "image_uri" {
  description = "ECR image URI for the api-gateway container."
  type        = string
}
variable "redis_url" {
  description = "Redis URL for state, queues, and approval audit."
  type        = string
  sensitive   = true
}
variable "canvas_api_url" {
  description = "Canvas instance base URL, e.g. https://campus.instructure.com."
  type        = string
}
variable "canvas_api_token_arn" {
  description = "Secrets Manager ARN for the Canvas API token."
  type        = string
}
variable "anthropic_api_key_arn" {
  description = "Secrets Manager ARN for the Anthropic API key."
  type        = string
}
variable "lti_signing_key_arn" {
  description = "Secrets Manager ARN for the LTI 1.3 RS256 private key."
  type        = string
}
variable "monthly_spend_cap_usd" {
  description = "Default per-course monthly Claude spend cap. 0=unlimited."
  type        = number
  default     = 0
}
variable "log_retention_days" {
  description = "CloudWatch Logs retention. 30d default keeps audit + crash forensics affordable."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------
# Cluster + task definition
# ---------------------------------------------------------------------------
resource "aws_ecs_cluster" "this" {
  name = "equalify-reflow-${var.tenant}"

  # Container Insights gives us per-task CPU/mem metrics in CloudWatch for free.
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/equalify-reflow/${var.tenant}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "task_execution" {
  name = "equalify-reflow-${var.tenant}-task-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Lets the execution role pull our three secrets at task startup.
resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "secrets-read"
  role = aws_iam_role.task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        var.canvas_api_token_arn,
        var.anthropic_api_key_arn,
        var.lti_signing_key_arn,
      ]
    }]
  })
}

# Task role - granted to the running container, used for runtime AWS calls
# (S3 reads/writes, Bedrock, etc.). Scope as tightly as your S3 buckets allow.
resource "aws_iam_role" "task_runtime" {
  name = "equalify-reflow-${var.tenant}-task-runtime"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_ecs_task_definition" "api_gateway" {
  family                   = "equalify-reflow-${var.tenant}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 1024  # 1 vCPU
  memory                   = 2048  # 2 GiB - enough for Docling sidecar workloads
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task_runtime.arn

  container_definitions = jsonencode([{
    name      = "api-gateway"
    image     = var.image_uri
    essential = true

    portMappings = [{
      containerPort = 8080
      protocol      = "tcp"
    }]

    environment = [
      { name = "CANVAS_TENANT", value = var.tenant },
      { name = "CANVAS_API_URL", value = var.canvas_api_url },
      { name = "ENVIRONMENT", value = "production" },
      { name = "LOG_FORMAT", value = "json" },
      { name = "REDIS_URL", value = var.redis_url },
      { name = "CANVAS_MONTHLY_SPEND_CAP_USD_DEFAULT", value = tostring(var.monthly_spend_cap_usd) },
    ]

    secrets = [
      { name = "CANVAS_API_TOKEN", valueFrom = var.canvas_api_token_arn },
      { name = "ANTHROPIC_API_KEY", valueFrom = var.anthropic_api_key_arn },
      { name = "LTI_PRIVATE_KEY", valueFrom = var.lti_signing_key_arn },
    ]

    # Pinned to /health/live (cheap, no deps). The ALB target group has its
    # own check against /health/ready (with deps); both must be healthy for
    # traffic to flow.
    healthCheck = {
      command     = ["CMD-SHELL", "curl -fsS http://localhost:8080/health/live || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  }])
}

# ---------------------------------------------------------------------------
# Service - 2 tasks across AZs for redundancy. Scales out under sustained
# CPU pressure. Deployment strategy is rolling with circuit breaker so a
# bad deploy auto-rolls back to the prior task definition.
# ---------------------------------------------------------------------------
resource "aws_ecs_service" "api_gateway" {
  name             = "equalify-reflow-${var.tenant}-api"
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.api_gateway.arn
  desired_count    = 2
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [aws_security_group.task.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api-gateway"
    container_port   = 8080
  }

  # The new task must pass health checks for 60s before old tasks are drained.
  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200
}

# Allow scaling between 2 and 6 tasks. Lower bound preserves redundancy
# (one task could be cycling for a deploy); upper bound caps blast radius
# if traffic spikes (or someone hits the spend cap path repeatedly).
resource "aws_appautoscaling_target" "api" {
  max_capacity       = 6
  min_capacity       = 2
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.api_gateway.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "cpu-target-tracking"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 65.0
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

# ---------------------------------------------------------------------------
# ALB + TLS termination
# ---------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "equalify-reflow-${var.tenant}-alb"
  description = "Public ALB ingress"
  vpc_id      = var.vpc_id

  ingress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "task" {
  name        = "equalify-reflow-${var.tenant}-task"
  description = "Fargate task ingress: only from the ALB SG"
  vpc_id      = var.vpc_id

  ingress {
    protocol        = "tcp"
    from_port       = 8080
    to_port         = 8080
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "this" {
  name               = "equalify-reflow-${var.tenant}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
}

resource "aws_lb_target_group" "api" {
  name        = "equalify-reflow-${var.tenant}-api"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  # /health/ready is the right probe for routing decisions: it returns 503
  # within 2 seconds when Redis is unreachable, so the ALB rapidly stops
  # sending traffic to a degraded task. /health/live is for ECS to decide
  # whether to RESTART (separate concern).
  health_check {
    path                = "/health/ready"
    port                = "8080"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
    matcher             = "200"
  }

  # Graceful drain so in-flight approvals finish before a task is killed.
  deregistration_delay = 30
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"

  # Operator supplies the cert via a data source or a separate
  # aws_acm_certificate resource. Leaving as variable so this module
  # doesn't presume cert lifecycle.
  ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn = var.tls_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

variable "tls_certificate_arn" {
  description = "ACM certificate ARN for the ALB HTTPS listener."
  type        = string
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
output "alb_dns_name" {
  description = "Public DNS name of the ALB; point your Canvas LTI tool config here."
  value       = aws_lb.this.dns_name
}
output "ecs_cluster_name" {
  description = "ECS cluster name for ops dashboards."
  value       = aws_ecs_cluster.this.name
}
output "log_group_name" {
  description = "CloudWatch Logs group - tail with `aws logs tail` for incident response."
  value       = aws_cloudwatch_log_group.app.name
}
