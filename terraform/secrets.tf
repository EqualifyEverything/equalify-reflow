# AWS Secrets Manager for Authentication Credentials
# Stores sensitive authentication values (API keys, passwords)
# ECS tasks retrieve these securely at runtime

# API Keys Secret
resource "aws_secretsmanager_secret" "api_keys" {
  name        = "${var.project_name}-api-keys"
  description = "Comma-separated list of valid API keys for ${var.project_name}"

  tags = {
    Name = "${var.project_name}-api-keys"
  }
}

resource "aws_secretsmanager_secret_version" "api_keys" {
  secret_id     = aws_secretsmanager_secret.api_keys.id
  secret_string = var.api_keys
}

# Docs Password Secret
resource "aws_secretsmanager_secret" "docs_password" {
  name        = "${var.project_name}-docs-password"
  description = "Password for Swagger/OpenAPI documentation access for ${var.project_name}"

  tags = {
    Name = "${var.project_name}-docs-password"
  }
}

resource "aws_secretsmanager_secret_version" "docs_password" {
  secret_id     = aws_secretsmanager_secret.docs_password.id
  secret_string = var.docs_password
}

# Grant ECS task execution role permission to read secrets
resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name = "${var.project_name}-ecs-task-execution-secrets"
  role = aws_iam_role.ecs_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.api_keys.arn,
          aws_secretsmanager_secret.docs_password.arn
        ]
      }
    ]
  })
}
