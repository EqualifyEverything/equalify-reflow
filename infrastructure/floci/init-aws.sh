#!/bin/bash

# Equalify Reflow — Floci S3 initialization script
#
# Run from a sidecar container (amazon/aws-cli) with:
#   AWS_ACCESS_KEY_ID=test
#   AWS_SECRET_ACCESS_KEY=test
#   AWS_DEFAULT_REGION=us-east-1
#   AWS_ENDPOINT_URL=http://floci:4566
#
# The awscli reads AWS_ENDPOINT_URL automatically, so every `aws` command
# below targets Floci instead of real AWS. The sidecar's compose
# `depends_on: floci (service_healthy)` gate replaces the old
# "wait for localstack ready.d" sleep pattern.

set -e

echo "=========================================="
echo "Initializing Floci for Equalify Reflow"
echo "=========================================="

TEMP_BUCKET="equalify-pdf-temp"
RESULTS_BUCKET="equalify-pdf-results"
REGION="us-east-1"

create_bucket() {
    local bucket_name=$1
    echo "Creating S3 bucket: ${bucket_name}"

    if aws s3 mb "s3://${bucket_name}" --region "${REGION}" 2>/dev/null; then
        echo "✓ Bucket ${bucket_name} created successfully"
    else
        echo "⚠ Bucket ${bucket_name} already exists or creation failed"
    fi
}

configure_cors() {
    local bucket_name=$1
    echo "Configuring CORS for bucket: ${bucket_name}"

    aws s3api put-bucket-cors --bucket "${bucket_name}" --cors-configuration '{
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

configure_public_read() {
    local bucket_name=$1
    echo "Configuring public read access for bucket: ${bucket_name}"

    aws s3api put-bucket-policy --bucket "${bucket_name}" --policy "{
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

enable_versioning() {
    local bucket_name=$1
    echo "Enabling versioning for bucket: ${bucket_name}"

    aws s3api put-bucket-versioning \
        --bucket "${bucket_name}" \
        --versioning-configuration Status=Enabled 2>/dev/null || \
        echo "⚠ Versioning configuration failed for ${bucket_name}"
}

echo ""
echo "Creating S3 buckets..."
create_bucket "${TEMP_BUCKET}"
create_bucket "${RESULTS_BUCKET}"

echo ""
echo "Configuring CORS..."
configure_cors "${TEMP_BUCKET}"
configure_cors "${RESULTS_BUCKET}"

echo ""
echo "Configuring bucket policies..."
configure_public_read "${RESULTS_BUCKET}"

echo ""
echo "Enabling versioning..."
enable_versioning "${RESULTS_BUCKET}"

echo ""
echo "Configuring lifecycle policies..."
aws s3api put-bucket-lifecycle-configuration \
    --bucket "${TEMP_BUCKET}" \
    --lifecycle-configuration '{
        "Rules": [
            {
                "ID": "DeleteTempFilesAfter7Days",
                "Status": "Enabled",
                "Prefix": "",
                "Expiration": {
                    "Days": 7
                }
            }
        ]
    }' 2>/dev/null || echo "⚠ Lifecycle policy configuration failed for ${TEMP_BUCKET}"

echo ""
echo "Verifying bucket creation..."
if aws s3 ls | grep -q "${TEMP_BUCKET}"; then
    echo "✓ ${TEMP_BUCKET} verified"
else
    echo "✗ ${TEMP_BUCKET} verification failed"
fi

if aws s3 ls | grep -q "${RESULTS_BUCKET}"; then
    echo "✓ ${RESULTS_BUCKET} verified"
else
    echo "✗ ${RESULTS_BUCKET} verification failed"
fi

echo ""
echo "Testing S3 upload capability..."
echo "Test file created by Floci init script" > /tmp/test.txt
if aws s3 cp /tmp/test.txt "s3://${TEMP_BUCKET}/test.txt" 2>/dev/null; then
    echo "✓ S3 upload test successful"
    aws s3 rm "s3://${TEMP_BUCKET}/test.txt" 2>/dev/null
    rm /tmp/test.txt
else
    echo "✗ S3 upload test failed"
fi

echo ""
echo "=========================================="
echo "Floci initialization complete!"
echo "=========================================="
echo ""
echo "Available S3 buckets:"
aws s3 ls
echo ""
echo "S3 endpoint: ${AWS_ENDPOINT_URL:-http://floci:4566}"
echo "=========================================="
