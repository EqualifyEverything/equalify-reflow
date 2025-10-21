# ACM Certificate for HTTPS
# Requires DNS validation via CNAME records

resource "aws_acm_certificate" "cert" {
  # Only create certificate if domain_name is provided
  count = var.domain_name != "" ? 1 : 0

  domain_name       = var.domain_name
  validation_method = "DNS"

  # Optional: Add subject alternative names for staging subdomain
  subject_alternative_names = var.staging_domain_name != "" ? [var.staging_domain_name] : []

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name        = "${var.project_name}-certificate"
    Environment = var.environment
  }
}

# ACM Certificate Validation
# This resource waits for DNS validation to complete
resource "aws_acm_certificate_validation" "cert" {
  count = var.domain_name != "" ? 1 : 0

  certificate_arn = aws_acm_certificate.cert[0].arn

  # If using Route 53 for DNS, validation can be automated
  # For external DNS (like UIC's), this will wait for manual DNS record creation

  timeouts {
    create = "30m" # Allow 30 minutes for DNS propagation
  }
}

# Output the DNS validation records
# Share these with your DNS administrator
output "acm_validation_records" {
  description = "DNS records needed for ACM certificate validation"
  value = var.domain_name != "" ? [
    for dvo in aws_acm_certificate.cert[0].domain_validation_options : {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      value  = dvo.resource_record_value
      domain = dvo.domain_name
    }
  ] : []
}

output "certificate_arn" {
  description = "ARN of the ACM certificate (for reference)"
  value       = var.domain_name != "" ? aws_acm_certificate.cert[0].arn : ""
}
