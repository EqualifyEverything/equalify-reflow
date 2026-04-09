# AWS CodeDeploy for ECS Blue-Green Deployments
#
# Provides zero-downtime deployments by spinning up a new task set (green),
# validating it via the test listener, shifting production traffic, then
# keeping old tasks (blue) alive for a configurable period so in-flight
# SSE streams can complete without interruption.

resource "aws_codedeploy_app" "app" {
  compute_platform = "ECS"
  name             = "${var.project_name}-deploy"

  tags = {
    Name = "${var.project_name}-codedeploy-app"
  }
}

resource "aws_codedeploy_deployment_group" "app" {
  app_name               = aws_codedeploy_app.app.name
  deployment_group_name  = "${var.project_name}-dg"
  deployment_config_name = var.deployment_strategy
  service_role_arn       = aws_iam_role.codedeploy.arn

  auto_rollback_configuration {
    enabled = true
    events  = ["DEPLOYMENT_FAILURE", "DEPLOYMENT_STOP_ON_ALARM"]
  }

  blue_green_deployment_config {
    deployment_ready_option {
      action_on_timeout    = "CONTINUE_DEPLOYMENT"
      wait_time_in_minutes = 0
    }

    terminate_blue_instances_on_deployment_success {
      action                           = "TERMINATE"
      termination_wait_time_in_minutes = var.deployment_termination_wait_minutes
    }
  }

  ecs_service {
    cluster_name = aws_ecs_cluster.main.name
    service_name = aws_ecs_service.app.name
  }

  deployment_style {
    deployment_option = "WITH_TRAFFIC_CONTROL"
    deployment_type   = "BLUE_GREEN"
  }

  load_balancer_info {
    target_group_pair_info {
      prod_traffic_route {
        listener_arns = [
          var.domain_name != "" ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
        ]
      }

      test_traffic_route {
        listener_arns = [aws_lb_listener.test.arn]
      }

      target_group {
        name = aws_lb_target_group.app.name
      }

      target_group {
        name = aws_lb_target_group.app_green.name
      }
    }
  }

  tags = {
    Name = "${var.project_name}-codedeploy-dg"
  }
}
