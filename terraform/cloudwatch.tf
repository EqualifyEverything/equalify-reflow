# CloudWatch Alarms and Monitoring

# SNS Topic for Alarms (optional)
resource "aws_sns_topic" "alarms" {
  count = var.enable_cloudwatch_alarms && var.alarm_email != "" ? 1 : 0
  name  = "${var.project_name}-alarms"

  tags = {
    Name = "${var.project_name}-alarms"
  }
}

resource "aws_sns_topic_subscription" "alarms_email" {
  count     = var.enable_cloudwatch_alarms && var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# High CPU Alarm
resource "aws_cloudwatch_metric_alarm" "ecs_high_cpu" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.project_name}-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "This metric monitors ECS CPU utilization"
  alarm_actions       = var.alarm_email != "" ? [aws_sns_topic.alarms[0].arn] : []

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.app.name
  }

  tags = {
    Name = "${var.project_name}-high-cpu-alarm"
  }
}

# High Memory Alarm
resource "aws_cloudwatch_metric_alarm" "ecs_high_memory" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.project_name}-high-memory"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = 300
  statistic           = "Average"
  threshold           = 85
  alarm_description   = "This metric monitors ECS memory utilization"
  alarm_actions       = var.alarm_email != "" ? [aws_sns_topic.alarms[0].arn] : []

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.app.name
  }

  tags = {
    Name = "${var.project_name}-high-memory-alarm"
  }
}

# ALB 5xx Errors
resource "aws_cloudwatch_metric_alarm" "alb_5xx_errors" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.project_name}-alb-5xx-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "This metric monitors ALB 5xx errors"
  alarm_actions       = var.alarm_email != "" ? [aws_sns_topic.alarms[0].arn] : []

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }

  tags = {
    Name = "${var.project_name}-alb-5xx-alarm"
  }
}

# Unhealthy Task Count
resource "aws_cloudwatch_metric_alarm" "unhealthy_tasks" {
  count               = var.enable_cloudwatch_alarms ? 1 : 0
  alarm_name          = "${var.project_name}-unhealthy-tasks"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Average"
  threshold           = 1
  alarm_description   = "This metric monitors healthy task count"
  alarm_actions       = var.alarm_email != "" ? [aws_sns_topic.alarms[0].arn] : []

  dimensions = {
    TargetGroup  = aws_lb_target_group.app.arn_suffix
    LoadBalancer = aws_lb.main.arn_suffix
  }

  tags = {
    Name = "${var.project_name}-unhealthy-tasks-alarm"
  }
}

# CloudWatch Dashboard for Bedrock and Infrastructure Monitoring
resource "aws_cloudwatch_dashboard" "bedrock_monitoring" {
  count          = var.enable_bedrock_metrics ? 1 : 0
  dashboard_name = "${var.project_name}-bedrock-monitoring"

  dashboard_body = jsonencode({
    widgets = [
      # Header
      {
        type = "text"
        x    = 0
        y    = 0
        width = 24
        height = 1
        properties = {
          markdown = "# Equalify PDF Converter - Bedrock & Infrastructure Monitoring\nReal-time monitoring of AWS Bedrock AI processing, ECS compute resources, and application performance."
        }
      },

      # Bedrock API Invocations
      {
        type = "metric"
        x    = 0
        y    = 1
        width = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/Bedrock", "Invocations", { stat = "Sum", label = "Total Invocations" }],
            [".", ".", { stat = "SampleCount", label = "Request Count" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Bedrock API Invocations"
          period  = 300
          yAxis = {
            left = {
              label = "Count"
              showUnits = false
            }
          }
        }
      },

      # Bedrock Latency
      {
        type = "metric"
        x    = 8
        y    = 1
        width = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/Bedrock", "InvocationLatency", { stat = "Average", label = "Avg Latency" }],
            ["...", { stat = "p50", label = "p50" }],
            ["...", { stat = "p90", label = "p90" }],
            ["...", { stat = "p99", label = "p99" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Bedrock Invocation Latency"
          period  = 300
          yAxis = {
            left = {
              label = "Milliseconds"
              showUnits = false
            }
          }
        }
      },

      # Bedrock Errors
      {
        type = "metric"
        x    = 16
        y    = 1
        width = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/Bedrock", "ModelInvocationClientError", { stat = "Sum", label = "Client Errors" }],
            [".", "ModelInvocationServerError", { stat = "Sum", label = "Server Errors" }],
            [".", "ThrottleExceptions", { stat = "Sum", label = "Throttles" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Bedrock Errors & Throttling"
          period  = 300
          yAxis = {
            left = {
              label = "Count"
              showUnits = false
            }
          }
        }
      },

      # Input Token Usage
      {
        type = "metric"
        x    = 0
        y    = 7
        width = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/Bedrock", "InputTokenCount", { stat = "Sum", label = "Total Input Tokens" }],
            ["...", { stat = "Average", label = "Avg Input Tokens per Request" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Bedrock Input Token Usage"
          period  = 300
          yAxis = {
            left = {
              label = "Tokens"
              showUnits = false
            }
          }
        }
      },

      # Output Token Usage
      {
        type = "metric"
        x    = 12
        y    = 7
        width = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/Bedrock", "OutputTokenCount", { stat = "Sum", label = "Total Output Tokens" }],
            ["...", { stat = "Average", label = "Avg Output Tokens per Request" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Bedrock Output Token Usage"
          period  = 300
          yAxis = {
            left = {
              label = "Tokens"
              showUnits = false
            }
          }
        }
      },

      # Cost Estimation Widget
      {
        type = "metric"
        x    = 0
        y    = 13
        width = 24
        height = 1
        properties = {
          markdown = "## Cost Estimation\n**Claude 3.5 Haiku Pricing:** $0.80/MTok input, $4.00/MTok output | **Target:** ~$0.20 per document | Monitor token usage above to estimate costs"
        }
      },

      # Combined Token Usage (for cost estimation)
      {
        type = "metric"
        x    = 0
        y    = 14
        width = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/Bedrock", "InputTokenCount", { stat = "Sum", label = "Input Tokens", id = "m1" }],
            [".", "OutputTokenCount", { stat = "Sum", label = "Output Tokens", id = "m2" }],
            [{ expression = "(m1*0.8 + m2*4)/1000000", label = "Estimated Cost ($)", id = "e1" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Token Usage & Estimated Cost"
          period  = 3600
          yAxis = {
            left = {
              label = "Tokens"
              showUnits = false
            }
            right = {
              label = "Cost (USD)"
              showUnits = false
            }
          }
        }
      },

      # Hourly Cost Estimation
      {
        type = "metric"
        x    = 12
        y    = 14
        width = 12
        height = 6
        properties = {
          metrics = [
            [{ expression = "(m1*0.8 + m2*4)/1000000", label = "Hourly Cost ($)", id = "e1" }],
            ["AWS/Bedrock", "InputTokenCount", { stat = "Sum", visible = false, id = "m1" }],
            [".", "OutputTokenCount", { stat = "Sum", visible = false, id = "m2" }]
          ]
          view    = "singleValue"
          region  = var.aws_region
          title   = "Estimated Hourly Bedrock Cost"
          period  = 3600
        }
      },

      # ECS Infrastructure Section Header
      {
        type = "text"
        x    = 0
        y    = 20
        width = 24
        height = 1
        properties = {
          markdown = "## ECS Infrastructure Metrics\nMonitoring compute resources, task health, and application performance."
        }
      },

      # ECS CPU Utilization
      {
        type = "metric"
        x    = 0
        y    = 21
        width = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", {
              stat       = "Average"
              dimensions = {
                ClusterName = aws_ecs_cluster.main.name
                ServiceName = aws_ecs_service.app.name
              }
            }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "ECS CPU Utilization"
          period  = 300
          yAxis = {
            left = {
              min = 0
              max = 100
              label = "Percent"
            }
          }
          annotations = {
            horizontal = [{
              value = 80
              label = "High CPU Threshold"
              fill  = "above"
              color = "#d62728"
            }]
          }
        }
      },

      # ECS Memory Utilization
      {
        type = "metric"
        x    = 8
        y    = 21
        width = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "MemoryUtilization", {
              stat       = "Average"
              dimensions = {
                ClusterName = aws_ecs_cluster.main.name
                ServiceName = aws_ecs_service.app.name
              }
            }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "ECS Memory Utilization"
          period  = 300
          yAxis = {
            left = {
              min = 0
              max = 100
              label = "Percent"
            }
          }
          annotations = {
            horizontal = [{
              value = 85
              label = "High Memory Threshold"
              fill  = "above"
              color = "#d62728"
            }]
          }
        }
      },

      # Task Count
      {
        type = "metric"
        x    = 16
        y    = 21
        width = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "RunningTaskCount", {
              stat       = "Average"
              dimensions = {
                ClusterName = aws_ecs_cluster.main.name
                ServiceName = aws_ecs_service.app.name
              }
            }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "ECS Running Tasks"
          period  = 300
          yAxis = {
            left = {
              min = 0
              label = "Count"
            }
          }
        }
      },

      # ALB Request Count
      {
        type = "metric"
        x    = 0
        y    = 27
        width = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", {
              stat       = "Sum"
              dimensions = {
                LoadBalancer = aws_lb.main.arn_suffix
              }
            }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "ALB Request Count"
          period  = 300
          yAxis = {
            left = {
              label = "Requests"
              showUnits = false
            }
          }
        }
      },

      # ALB Response Times
      {
        type = "metric"
        x    = 8
        y    = 27
        width = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", {
              stat       = "Average"
              dimensions = {
                LoadBalancer = aws_lb.main.arn_suffix
              }
              label = "Avg Response Time"
            }],
            ["...", {
              stat       = "p90"
              dimensions = {
                LoadBalancer = aws_lb.main.arn_suffix
              }
              label = "p90"
            }],
            ["...", {
              stat       = "p99"
              dimensions = {
                LoadBalancer = aws_lb.main.arn_suffix
              }
              label = "p99"
            }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "ALB Response Time"
          period  = 300
          yAxis = {
            left = {
              label = "Seconds"
              showUnits = false
            }
          }
        }
      },

      # ALB HTTP Status Codes
      {
        type = "metric"
        x    = 16
        y    = 27
        width = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_2XX_Count", {
              stat       = "Sum"
              dimensions = {
                LoadBalancer = aws_lb.main.arn_suffix
              }
              label = "2xx Success"
            }],
            [".", "HTTPCode_Target_4XX_Count", {
              stat       = "Sum"
              dimensions = {
                LoadBalancer = aws_lb.main.arn_suffix
              }
              label = "4xx Client Errors"
            }],
            [".", "HTTPCode_Target_5XX_Count", {
              stat       = "Sum"
              dimensions = {
                LoadBalancer = aws_lb.main.arn_suffix
              }
              label = "5xx Server Errors"
            }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "ALB HTTP Status Codes"
          period  = 300
          yAxis = {
            left = {
              label = "Count"
              showUnits = false
            }
          }
        }
      },

      # Healthy Target Count
      {
        type = "metric"
        x    = 0
        y    = 33
        width = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ApplicationELB", "HealthyHostCount", {
              stat       = "Average"
              dimensions = {
                TargetGroup  = aws_lb_target_group.app.arn_suffix
                LoadBalancer = aws_lb.main.arn_suffix
              }
              label = "Healthy Hosts"
            }],
            [".", "UnHealthyHostCount", {
              stat       = "Average"
              dimensions = {
                TargetGroup  = aws_lb_target_group.app.arn_suffix
                LoadBalancer = aws_lb.main.arn_suffix
              }
              label = "Unhealthy Hosts"
            }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "ALB Target Health"
          period  = 60
          yAxis = {
            left = {
              min = 0
              label = "Count"
            }
          }
        }
      },

      # Correlation: CPU vs Bedrock Invocations
      {
        type = "metric"
        x    = 12
        y    = 33
        width = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", {
              stat       = "Average"
              dimensions = {
                ClusterName = aws_ecs_cluster.main.name
                ServiceName = aws_ecs_service.app.name
              }
              label = "CPU %"
              yAxis = "left"
            }],
            ["AWS/Bedrock", "Invocations", {
              stat  = "Sum"
              label = "Bedrock Invocations"
              yAxis = "right"
            }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "CPU Utilization vs Bedrock Activity"
          period  = 300
          yAxis = {
            left = {
              label = "CPU %"
              showUnits = false
            }
            right = {
              label = "Invocations"
              showUnits = false
            }
          }
        }
      }
    ]
  })
}

# Dashboard Output
output "bedrock_dashboard_url" {
  description = "URL to CloudWatch Dashboard for Bedrock monitoring"
  value = var.enable_bedrock_metrics ? "https://console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.bedrock_monitoring[0].dashboard_name}" : "Dashboard disabled (enable_bedrock_metrics=false)"
}
