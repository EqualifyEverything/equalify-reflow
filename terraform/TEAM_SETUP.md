# Terraform Setup Guide for Team Members

This guide helps team members configure AWS credentials for Terraform deployments.

## Quick Start

### 1. Configure AWS Credentials

You have **two options** depending on your access type:

#### Option A: AWS SSO (UIC Team Members)

If you have UIC AWS SSO access:

```bash
# Configure SSO
aws configure sso

# Enter these values:
SSO session name: uic
SSO start URL: https://d-9a672cc795.awsapps.com/start
SSO region: us-east-2
SSO registration scopes: [press Enter]

# Browser will open - authorize the request
# Select the Equalify AWS account and AWSAdministratorAccess role

# Then:
CLI default region: us-east-1
CLI output format: json
CLI profile name: uic
```

**Set environment variable:**
```bash
export AWS_PROFILE=uic
```

**Verify:**
```bash
aws sts get-caller-identity
# Should show your user ARN and the Equalify account information
```

#### Option B: IAM User (External Collaborators)

If IT created an IAM user for you:

```bash
aws configure

# Enter your credentials:
AWS Access Key ID: AKIA...
AWS Secret Access Key: ...
Default region: us-east-1
Default output format: json
```

### 2. Configure Terraform Variables

```bash
cd terraform

# Copy example file
cp terraform.tfvars.example terraform.tfvars

# Edit with your values
vim terraform.tfvars  # or code, nano, etc.
```

**Required values:**
```hcl
aws_region  = "us-east-1"
environment = "production"
alarm_email = "your-email@uic.edu"

# Leave aws_profile empty if using AWS_PROFILE env var
aws_profile = ""
```

### 3. Initialize Terraform

```bash
terraform init
```

### 4. Verify Configuration

```bash
# Test AWS connection
terraform plan

# Should show infrastructure changes without errors
```

## Credential Management Approaches

### Recommended: Environment Variable (Team Standard)

**Pros:**
- ✅ Works for SSO and IAM users
- ✅ No secrets in files
- ✅ Easy to switch environments
- ✅ CI/CD compatible

**Setup:**
```bash
# Add to your shell profile (~/.zshrc or ~/.bashrc)
echo 'export AWS_PROFILE=uic' >> ~/.zshrc
source ~/.zshrc
```

### Alternative: Direct Configuration

If you prefer setting profile in `terraform.tfvars`:

```hcl
# terraform/terraform.tfvars
aws_profile = "uic"
```

**Note:** Don't commit this file to git (already in `.gitignore`)

## Team Workflows

### Daily Development

```bash
cd terraform

# Ensure AWS profile is set
echo $AWS_PROFILE  # Should show: uic

# Make infrastructure changes
vim main.tf

# Review changes
terraform plan

# Apply changes
terraform apply
```

### Switching Between Environments

```bash
# Development
export AWS_PROFILE=uic-dev
terraform workspace select dev

# Production
export AWS_PROFILE=uic
terraform workspace select production
```

### CI/CD (GitHub Actions)

GitHub Actions uses IAM roles (no profile needed):
- Configured in `.github/workflows/deploy.yml`
- Uses OIDC role assumption
- No access keys stored in secrets

## Troubleshooting

### Error: "No credentials found"

```bash
# Check profile is set
echo $AWS_PROFILE

# If empty, set it
export AWS_PROFILE=uic

# Verify AWS CLI works
aws sts get-caller-identity
```

### Error: "SSO session expired"

```bash
# Re-login to SSO
aws sso login --profile uic
```

### Error: "Access denied"

Contact team lead to verify your AWS permissions include:
- AmazonEC2FullAccess
- AmazonECS_FullAccess
- AmazonS3FullAccess
- ElastiCacheFullAccess
- IAMFullAccess
- AmazonVPCFullAccess
- CloudWatchLogsFullAccess

## Security Best Practices

### DO ✅

- Use SSO when available
- Use environment variables for profiles
- Rotate access keys every 90 days (IAM users)
- Enable MFA on AWS account
- Keep `terraform.tfvars` out of git
- Use separate profiles for dev/prod

### DON'T ❌

- Don't commit `terraform.tfvars` to git
- Don't commit `.tfstate` files to git
- Don't share access keys in Slack/email
- Don't hardcode credentials in code
- Don't use root AWS account credentials

## Getting Help

**Questions?** Contact:
- Dylan Isaac (disaac4@uic.edu)
- Blake Bertuccelli-Booth (b3b@uic.edu)
- UIC IT Support

**Issues?**
- Check AWS Console: https://console.aws.amazon.com/
- Review logs: `terraform plan -json | jq`
- Check state: `terraform state list`

---

**Last Updated:** 2025-10-07
**Maintained by:** UIC DASE Engineering Team
