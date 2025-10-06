# AWS Bedrock Configuration and IAM Permissions

# Bedrock Access Policy for ECS Task Role
resource "aws_iam_role_policy" "ecs_task_bedrock" {
  name = "${var.project_name}-ecs-task-bedrock"
  role = aws_iam_role.ecs_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-3-5-haiku-*",
          "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-3-5-sonnet-*",
          "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-3-opus-*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModel"
        ]
        Resource = "*"
      }
    ]
  })
}

# CloudWatch Metrics for Bedrock Usage (optional)
resource "aws_cloudwatch_log_metric_filter" "bedrock_token_usage" {
  count          = var.enable_bedrock_metrics ? 1 : 0
  name           = "${var.project_name}-bedrock-tokens"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "[timestamp, request_id, level, msg=*tokens*]"

  metric_transformation {
    name      = "BedrockTokenUsage"
    namespace = "${var.project_name}/Bedrock"
    value     = "1"
  }
}

# Alarm for Bedrock Throttling (optional)
resource "aws_cloudwatch_metric_alarm" "bedrock_throttling" {
  count               = var.enable_cloudwatch_alarms && var.enable_bedrock_metrics ? 1 : 0
  alarm_name          = "${var.project_name}-bedrock-throttling"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ModelInvocationClientError"
  namespace           = "AWS/Bedrock"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "This metric monitors Bedrock throttling errors"
  alarm_actions       = var.alarm_email != "" ? [aws_sns_topic.alarms[0].arn] : []

  dimensions = {
    ModelId = var.bedrock_model_id
  }

  tags = {
    Name = "${var.project_name}-bedrock-throttling-alarm"
  }
}
