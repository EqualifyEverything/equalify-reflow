# Docling-serve — Decoupled ECS Service on EC2 GPU Spot (PRD-2/3/4)
#
# Runs docling-serve CUDA on g4dn.xlarge (GPU, Spot) behind an internal ALB.
# Scale-to-zero when idle, scale-out on job submission. Decoupled from the API
# Fargate task to allow independent scaling.

# ---------------------------------------------------------------------------
# AMI — ECS GPU-optimized Amazon Linux 2 x86_64
# ---------------------------------------------------------------------------

data "aws_ami" "ecs_gpu_optimized" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2-ami-ecs-gpu-hvm-*-x86_64-ebs"]
  }
}

# ---------------------------------------------------------------------------
# IAM — EC2 instance role for ECS
# ---------------------------------------------------------------------------

resource "aws_iam_role" "docling_instance_role" {
  name = "${var.project_name}-docling-instance-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-docling-instance-role"
  }
}

resource "aws_iam_role_policy_attachment" "docling_instance_ecs" {
  role       = aws_iam_role.docling_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_instance_profile" "docling" {
  name = "${var.project_name}-docling-instance-profile"
  role = aws_iam_role.docling_instance_role.name
}

# ---------------------------------------------------------------------------
# CloudWatch Log Group
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "docling" {
  name              = "/ecs/${var.project_name}-docling"
  retention_in_days = 30

  tags = {
    Name = "${var.project_name}-docling-logs"
  }
}

# ---------------------------------------------------------------------------
# Launch Template — GPU (instance type managed by ASG mixed policy)
# ---------------------------------------------------------------------------

resource "aws_launch_template" "docling" {
  name_prefix = "${var.project_name}-docling-"
  image_id    = data.aws_ami.ecs_gpu_optimized.id

  iam_instance_profile {
    arn = aws_iam_instance_profile.docling.arn
  }

  vpc_security_group_ids = [aws_security_group.docling_ec2.id]

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 50 # CUDA image ~8-10 GB + models
      volume_type = "gp3"
    }
  }

  user_data = base64encode(<<-EOF
    #!/bin/bash
    echo "ECS_CLUSTER=${aws_ecs_cluster.main.name}" >> /etc/ecs/ecs.config
    echo "ECS_ENABLE_SPOT_INSTANCE_DRAINING=true" >> /etc/ecs/ecs.config
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.project_name}-docling"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ---------------------------------------------------------------------------
# Auto Scaling Group
# ---------------------------------------------------------------------------

resource "aws_autoscaling_group" "docling" {
  name_prefix           = "${var.project_name}-docling-"
  min_size              = 0
  max_size              = var.docling_max_capacity
  desired_capacity      = var.docling_min_capacity
  vpc_zone_identifier   = module.vpc.private_subnets
  capacity_rebalance    = true
  protect_from_scale_in = true # Required for ECS managed termination protection

  mixed_instances_policy {
    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.docling.id
        version            = "$Latest"
      }

      override {
        instance_type = var.docling_instance_type # g4dn.xlarge (primary)
      }

      override {
        instance_type = "g4dn.2xlarge" # fallback
      }
    }

    instances_distribution {
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = 0 # 100% Spot
      spot_allocation_strategy                 = "capacity-optimized"
    }
  }

  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }

  tag {
    key                 = "Name"
    value               = "${var.project_name}-docling"
    propagate_at_launch = true
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ---------------------------------------------------------------------------
# ECS Capacity Provider + Cluster Attachment
# ---------------------------------------------------------------------------

resource "aws_ecs_capacity_provider" "docling" {
  name = "${var.project_name}-docling-cp"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.docling.arn
    managed_termination_protection = "ENABLED"

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 1
    }
  }

  tags = {
    Name = "${var.project_name}-docling-cp"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = [
    "FARGATE",
    aws_ecs_capacity_provider.docling.name,
  ]

  # Default to FARGATE so existing API service isn't disrupted
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ---------------------------------------------------------------------------
# Security Groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "docling_alb" {
  name_prefix = "${var.project_name}-docling-alb-"
  description = "Security group for docling internal ALB"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "Docling from VPC"
    from_port   = 5001
    to_port     = 5001
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description     = "To docling EC2 instances"
    from_port       = 5001
    to_port         = 5001
    protocol        = "tcp"
    security_groups = [aws_security_group.docling_ec2.id]
  }

  tags = {
    Name = "${var.project_name}-docling-alb-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "docling_ec2" {
  name_prefix = "${var.project_name}-docling-ec2-"
  description = "Security group for docling EC2 instances"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "Docling from ALB"
    from_port   = 5001
    to_port     = 5001
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "All outbound (ECR pulls, ECS agent, CloudWatch)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-docling-ec2-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ---------------------------------------------------------------------------
# Internal ALB + Target Group + Listener
# ---------------------------------------------------------------------------

resource "aws_lb" "docling_internal" {
  name               = "${var.project_name}-docling-alb"
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.docling_alb.id]
  subnets            = module.vpc.private_subnets
  idle_timeout       = 360 # Must exceed client 300s timeout

  tags = {
    Name = "${var.project_name}-docling-alb"
  }
}

resource "aws_lb_target_group" "docling" {
  name        = "${var.project_name}-docling-tg"
  port        = 5001
  protocol    = "HTTP"
  vpc_id      = module.vpc.vpc_id
  target_type = "instance"

  health_check {
    path                = "/health"
    port                = "5001"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 5
    timeout             = 10
    interval            = 30
    matcher             = "200"
  }

  deregistration_delay = 30

  tags = {
    Name = "${var.project_name}-docling-tg"
  }
}

resource "aws_lb_listener" "docling" {
  load_balancer_arn = aws_lb.docling_internal.arn
  port              = 5001
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.docling.arn
  }
}

# ---------------------------------------------------------------------------
# ECS Task Definition (EC2 launch type)
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "docling" {
  family                   = "${var.project_name}-docling"
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn

  container_definitions = jsonencode([
    {
      name      = "docling-serve"
      image     = var.docling_serve_image != "" ? var.docling_serve_image : "${aws_ecr_repository.docling.repository_url}:${var.docling_serve_image_tag}"
      essential = true
      cpu       = 4096
      memory    = 14336 # g4dn.xlarge has 16 GB, leave headroom

      resourceRequirements = [
        {
          type  = "GPU"
          value = "1"
        }
      ]

      portMappings = [
        {
          containerPort = 5001
          hostPort      = 5001
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "DOCLING_SERVE_LOAD_MODELS_AT_BOOT"
          value = "true"
        },
        {
          name  = "DOCLING_SERVE_ENG_LOC_SHARE_MODELS"
          value = "true"
        },
        {
          name  = "DOCLING_SERVE_MAX_SYNC_WAIT"
          value = "600"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.docling.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "docling"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:5001/health || exit 1"]
        interval    = 30
        timeout     = 10
        retries     = 5
        startPeriod = 180 # 3min for model loading
      }
    }
  ])

  tags = {
    Name = "${var.project_name}-docling-task"
  }
}

# ---------------------------------------------------------------------------
# ECS Service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "docling" {
  name            = "${var.project_name}-docling-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.docling.arn

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.docling.name
    weight            = 1
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.docling.arn
    container_name   = "docling-serve"
    container_port   = 5001
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 0 # Required for scale-from-zero

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [
    aws_lb_listener.docling,
    aws_ecs_cluster_capacity_providers.main,
  ]

  # Let auto-scaling manage desired_count
  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = {
    Name = "${var.project_name}-docling-service"
  }
}

# ---------------------------------------------------------------------------
# Auto Scaling — ECS Service
# ---------------------------------------------------------------------------

resource "aws_appautoscaling_target" "docling" {
  max_capacity       = var.docling_max_capacity
  min_capacity       = var.docling_min_capacity
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.docling.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "docling_scale_out" {
  name               = "${var.project_name}-docling-scale-out"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.docling.resource_id
  scalable_dimension = aws_appautoscaling_target.docling.scalable_dimension
  service_namespace  = aws_appautoscaling_target.docling.service_namespace

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 120
    metric_aggregation_type = "Maximum"

    step_adjustment {
      scaling_adjustment          = 1
      metric_interval_lower_bound = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "docling_jobs" {
  alarm_name          = "${var.project_name}-docling-jobs-in-processing"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "JobsInProcessing"
  namespace           = "EqualifyPDF"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1
  alarm_description   = "Scale out docling when jobs are being processed"
  treat_missing_data  = "notBreaching" # Silence = no jobs = don't alarm

  alarm_actions = [aws_appautoscaling_policy.docling_scale_out.arn]

  tags = {
    Name = "${var.project_name}-docling-scaling-alarm"
  }
}
