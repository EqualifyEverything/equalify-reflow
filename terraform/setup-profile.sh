#!/bin/bash
# Setup AWS Profile for Terraform
# Usage: ./setup-profile.sh

set -e

echo "🔧 Equalify PDF Converter - AWS Profile Setup"
echo "================================================"
echo ""

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI is not installed"
    echo "Install it from: https://aws.amazon.com/cli/"
    exit 1
fi

echo "✅ AWS CLI found: $(aws --version)"
echo ""

# Check current AWS credentials
echo "Checking current AWS configuration..."
if aws sts get-caller-identity &> /dev/null; then
    echo "✅ AWS credentials are already configured!"
    aws sts get-caller-identity
    echo ""
    read -p "Do you want to reconfigure? (y/N): " confirm
    if [[ ! $confirm =~ ^[Yy]$ ]]; then
        echo "Keeping existing configuration."
        exit 0
    fi
fi

echo ""
echo "Choose your access method:"
echo "1) AWS SSO (UIC team members)"
echo "2) IAM User (external collaborators)"
read -p "Enter choice (1 or 2): " choice

if [ "$choice" = "1" ]; then
    echo ""
    echo "📝 Configuring AWS SSO..."
    echo ""
    echo "When prompted, use these values:"
    echo "  SSO session name: uic"
    echo "  SSO start URL: https://d-9a672cc795.awsapps.com/start"
    echo "  SSO region: us-east-2"
    echo "  SSO registration scopes: [just press Enter]"
    echo ""
    echo "  CLI default region: us-east-1"
    echo "  CLI output format: json"
    echo "  CLI profile name: uic"
    echo ""
    read -p "Press Enter to continue..."

    aws configure sso

    # Test the connection
    echo ""
    echo "Testing SSO connection..."
    export AWS_PROFILE=uic
    if aws sts get-caller-identity; then
        echo ""
        echo "✅ SSO configured successfully!"
        echo ""
        echo "Add this to your ~/.zshrc or ~/.bashrc:"
        echo "  export AWS_PROFILE=uic"
    fi

elif [ "$choice" = "2" ]; then
    echo ""
    echo "📝 Configuring IAM User..."
    echo "You'll need:"
    echo "  - AWS Access Key ID"
    echo "  - AWS Secret Access Key"
    echo ""
    read -p "Press Enter to continue..."

    aws configure

    # Test the connection
    echo ""
    echo "Testing IAM credentials..."
    if aws sts get-caller-identity; then
        echo ""
        echo "✅ IAM credentials configured successfully!"
    fi
else
    echo "❌ Invalid choice"
    exit 1
fi

echo ""
echo "================================================"
echo "✅ Setup Complete!"
echo ""
echo "Next steps:"
echo "  1. cd terraform"
echo "  2. terraform init"
echo "  3. terraform plan"
echo ""
