# IAM Roles and Policies for ECS

# ECS Task Execution Role (for pulling images, writing logs)
resource "aws_iam_role" "ecs_execution_role" {
  name = "${var.project_name}-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-ecs-execution-role"
  }
}

resource "aws_iam_role_policy_attachment" "ecs_execution_role_policy" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ECS Task Role (for application permissions)
resource "aws_iam_role" "ecs_task_role" {
  name = "${var.project_name}-ecs-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-ecs-task-role"
  }
}

# S3 Access Policy for Task Role
resource "aws_iam_role_policy" "ecs_task_s3" {
  name = "${var.project_name}-ecs-task-s3"
  role = aws_iam_role.ecs_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:HeadBucket",
          "s3:GetBucketLocation"
        ]
        Resource = [
          aws_s3_bucket.temp.arn,
          "${aws_s3_bucket.temp.arn}/*",
          aws_s3_bucket.results.arn,
          "${aws_s3_bucket.results.arn}/*"
        ]
      }
    ]
  })
}

# CloudWatch Logs + Metrics Policy for Task Role
resource "aws_iam_role_policy" "ecs_task_cloudwatch" {
  name = "${var.project_name}-ecs-task-cloudwatch"
  role = aws_iam_role.ecs_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.app.arn}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "EqualifyPDF"
          }
        }
      }
    ]
  })
}

# SSM Permissions for ECS Exec (allows container to communicate with Session Manager)
# This lets the container "answer the phone" when you try to exec into it
resource "aws_iam_role_policy" "ecs_task_ssm" {
  name = "${var.project_name}-ecs-task-ssm"
  role = aws_iam_role.ecs_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel", # Opens a control channel for commands
          "ssmmessages:CreateDataChannel",    # Opens a data channel for input/output
          "ssmmessages:OpenControlChannel",   # Keeps control channel open
          "ssmmessages:OpenDataChannel"       # Keeps data channel open
        ]
        Resource = "*" # These are session-level permissions, no specific resource
      }
    ]
  })
}

# IAM Policy for User Access Control (attach to specific IAM users/roles)
# This controls WHO can actually connect via exec
resource "aws_iam_policy" "ecs_exec_access" {
  name        = "${var.project_name}-ecs-exec-access"
  description = "Allows ECS exec access for authorized users (debugging production)"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecs:ExecuteCommand",    # Permission to run exec command
          "ecs:DescribeTasks",     # Permission to see task details
          "ecs:DescribeServices"   # Permission to see service info
        ]
        Resource = [
          "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.main.name}/*",
          "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:service/${aws_ecs_cluster.main.name}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:StartSession" # Permission to start an SSM session
        ]
        Resource = [
          "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.main.name}/*",
          "arn:aws:ssm:${var.aws_region}::document/AmazonECS-ExecuteInteractiveCommand"
        ]
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-ecs-exec-access"
  }
}

# Output the policy ARN so you know what to attach to users
# You'll manually attach this to IAM users who need exec access
output "ecs_exec_policy_arn" {
  description = "IAM policy ARN for ECS exec access - attach this to users who need shell access"
  value       = aws_iam_policy.ecs_exec_access.arn
}
