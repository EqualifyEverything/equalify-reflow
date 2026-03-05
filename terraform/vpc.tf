# VPC and Networking Configuration

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.project_name}-vpc"
  cidr = var.vpc_cidr

  azs = slice(
    data.aws_availability_zones.available.names,
    0,
    var.availability_zones_count
  )

  # Public subnets for ALB
  public_subnets = [
    for i in range(var.availability_zones_count) :
    cidrsubnet(var.vpc_cidr, 8, i)
  ]

  # Private subnets for ECS tasks and Redis
  private_subnets = [
    for i in range(var.availability_zones_count) :
    cidrsubnet(var.vpc_cidr, 8, i + 100)
  ]

  # Enable NAT Gateway for private subnet internet access
  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true
  enable_dns_support   = true

  # VPC Flow Logs (optional, recommended for production)
  enable_flow_log                      = var.environment == "production"
  create_flow_log_cloudwatch_iam_role  = var.environment == "production"
  create_flow_log_cloudwatch_log_group = var.environment == "production"

  tags = {
    Name = "${var.project_name}-vpc"
  }

  public_subnet_tags = {
    Type = "public"
  }

  private_subnet_tags = {
    Type = "private"
  }
}
