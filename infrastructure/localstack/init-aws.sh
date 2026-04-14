#!/bin/bash

# Equalify Reflow - LocalStack Initialization Script
# This script runs automatically when LocalStack starts (ready.d hook)
# Creates S3 buckets and configures initial AWS resources

set -e

echo "=========================================="
echo "Initializing LocalStack for Equalify Reflow"
echo "=========================================="

# Configuration
TEMP_BUCKET="equalify-pdf-temp"
RESULTS_BUCKET="equalify-pdf-results"
REGION="us-east-1"

# Function to create S3 bucket with error handling
create_bucket() {
    local bucket_name=$1
    echo "Creating S3 bucket: ${bucket_name}"

    if awslocal s3 mb "s3://${bucket_name}" --region "${REGION}" 2>/dev/null; then
        echo "✓ Bucket ${bucket_name} created successfully"
    else
        echo "⚠ Bucket ${bucket_name} already exists or creation failed"
    fi
}

# Function to configure bucket CORS
configure_cors() {
    local bucket_name=$1
    echo "Configuring CORS for bucket: ${bucket_name}"

    awslocal s3api put-bucket-cors --bucket "${bucket_name}" --cors-configuration '{
        "CORSRules": [
            {
                "AllowedOrigins": ["*"],
                "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
                "AllowedHeaders": ["*"],
                "ExposeHeaders": ["ETag"],
                "MaxAgeSeconds": 3000
            }
        ]
    }' 2>/dev/null || echo "⚠ CORS configuration failed for ${bucket_name}"
}

# Function to set bucket policy for public read on results
configure_public_read() {
    local bucket_name=$1
    echo "Configuring public read access for bucket: ${bucket_name}"

    awslocal s3api put-bucket-policy --bucket "${bucket_name}" --policy "{
        \"Version\": \"2012-10-17\",
        \"Statement\": [
            {
                \"Sid\": \"PublicReadGetObject\",
                \"Effect\": \"Allow\",
                \"Principal\": \"*\",
                \"Action\": \"s3:GetObject\",
                \"Resource\": \"arn:aws:s3:::${bucket_name}/*\"
            }
        ]
    }" 2>/dev/null || echo "⚠ Public read policy configuration failed for ${bucket_name}"
}

# Function to enable versioning
enable_versioning() {
    local bucket_name=$1
    echo "Enabling versioning for bucket: ${bucket_name}"

    awslocal s3api put-bucket-versioning \
        --bucket "${bucket_name}" \
        --versioning-configuration Status=Enabled 2>/dev/null || \
        echo "⚠ Versioning configuration failed for ${bucket_name}"
}

# Wait for LocalStack to be fully ready
echo "Waiting for LocalStack S3 service to be ready..."
sleep 2

# Create S3 buckets
echo ""
echo "Creating S3 buckets..."
create_bucket "${TEMP_BUCKET}"
create_bucket "${RESULTS_BUCKET}"

# Configure CORS for both buckets
echo ""
echo "Configuring CORS..."
configure_cors "${TEMP_BUCKET}"
configure_cors "${RESULTS_BUCKET}"

# Configure public read access for results bucket
echo ""
echo "Configuring bucket policies..."
configure_public_read "${RESULTS_BUCKET}"

# Enable versioning for results bucket
echo ""
echo "Enabling versioning..."
enable_versioning "${RESULTS_BUCKET}"

# Create lifecycle policy for temp bucket (auto-delete after 7 days)
echo ""
echo "Configuring lifecycle policies..."
awslocal s3api put-bucket-lifecycle-configuration \
    --bucket "${TEMP_BUCKET}" \
    --lifecycle-configuration '{
        "Rules": [
            {
                "Id": "DeleteTempFilesAfter7Days",
                "Status": "Enabled",
                "Prefix": "",
                "Expiration": {
                    "Days": 7
                }
            }
        ]
    }' 2>/dev/null || echo "⚠ Lifecycle policy configuration failed for ${TEMP_BUCKET}"

# Verify bucket creation
echo ""
echo "Verifying bucket creation..."
if awslocal s3 ls | grep -q "${TEMP_BUCKET}"; then
    echo "✓ ${TEMP_BUCKET} verified"
else
    echo "✗ ${TEMP_BUCKET} verification failed"
fi

if awslocal s3 ls | grep -q "${RESULTS_BUCKET}"; then
    echo "✓ ${RESULTS_BUCKET} verified"
else
    echo "✗ ${RESULTS_BUCKET} verification failed"
fi

# Create test file to verify upload capability
echo ""
echo "Testing S3 upload capability..."
echo "Test file created by LocalStack init script" > /tmp/test.txt
if awslocal s3 cp /tmp/test.txt "s3://${TEMP_BUCKET}/test.txt" 2>/dev/null; then
    echo "✓ S3 upload test successful"
    awslocal s3 rm "s3://${TEMP_BUCKET}/test.txt" 2>/dev/null
    rm /tmp/test.txt
else
    echo "✗ S3 upload test failed"
fi

echo ""
echo "=========================================="
echo "LocalStack initialization complete!"
echo "=========================================="
echo ""
echo "Available S3 buckets:"
awslocal s3 ls
echo ""
echo "S3 endpoint: http://localhost:4566"
echo "=========================================="