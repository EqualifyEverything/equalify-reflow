# AWS Secrets Manager for Sensitive Configuration

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = "${var.project_name}-anthropic-api-key"
  description             = "Anthropic API key for AI processing"
  recovery_window_in_days = 7

  tags = {
    Name = "${var.project_name}-anthropic-api-key"
  }
}

resource "aws_secretsmanager_secret_version" "anthropic_api_key" {
  count         = var.anthropic_api_key != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.anthropic_api_key.id
  secret_string = var.anthropic_api_key
}

# Note: If anthropic_api_key is not provided via variable, you'll need to manually set it:
# aws secretsmanager put-secret-value --secret-id <secret-name> --secret-string "your-api-key"
