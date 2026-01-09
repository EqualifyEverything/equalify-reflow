# AWS Budget Alerts for Cost Protection
# Provides financial circuit breaker for Bedrock and overall costs
#
# NOTE: This is a linked account in UIC's AWS Organization.
# Cost allocation tags must be activated by the payer account admin.
# As a workaround, budgets filter by region (us-east-1) to exclude
# other projects' resources in us-east-2.

# Daily Bedrock spend alert
# Note: Bedrock costs are tracked by service since Bedrock API calls
# don't inherit resource tags. This tracks all Bedrock usage in the region.
resource "aws_budgets_budget" "bedrock_daily" {
  name         = "${var.project_name}-bedrock-daily"
  budget_type  = "COST"
  limit_amount = var.bedrock_daily_budget_limit
  limit_unit   = "USD"
  time_unit    = "DAILY"

  # Filter by Bedrock service in our region
  cost_filter {
    name   = "Service"
    values = ["Amazon Bedrock"]
  }

  cost_filter {
    name   = "Region"
    values = [var.aws_region]
  }

  # Alert at 50% of daily budget
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alarm_email != "" ? [var.alarm_email] : []
  }

  # Alert at 80% of daily budget
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alarm_email != "" ? [var.alarm_email] : []
  }

  # Alert when exceeded
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alarm_email != "" ? [var.alarm_email] : []
  }

  tags = {
    Name        = "${var.project_name}-bedrock-daily-budget"
    Description = "Daily cost protection for Bedrock AI processing"
  }
}

# Monthly budget for PDF converter infrastructure (us-east-1)
# 
# NOTE: This account is part of UIC's AWS Organization. Cost allocation tags
# can only be activated by the payer account admin. As a workaround, we filter
# by region (us-east-1) since our infrastructure is isolated there.
# 
# If UIC activates the "Project" tag, you can switch to tag-based filtering:
#   cost_filter {
#     name   = "TagKeyValue"
#     values = ["user:Project$${var.project_name}"]
#   }
resource "aws_budgets_budget" "monthly_total" {
  name         = "${var.project_name}-monthly-total"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_limit
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Filter to us-east-1 only (our infrastructure)
  # This excludes us-east-2 resources (DASE Equalify: RDS, Transit Gateway, etc.)
  cost_filter {
    name   = "Region"
    values = [var.aws_region]
  }

  # Alert at 50% of monthly budget
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alarm_email != "" ? [var.alarm_email] : []
  }

  # Alert at 80% of monthly budget
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alarm_email != "" ? [var.alarm_email] : []
  }

  # Alert when exceeded
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alarm_email != "" ? [var.alarm_email] : []
  }

  # Forecasted overspend warning
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alarm_email != "" ? [var.alarm_email] : []
  }

  tags = {
    Name        = "${var.project_name}-monthly-budget"
    Description = "Monthly cost protection for ${var.project_name} in ${var.aws_region}"
  }
}
