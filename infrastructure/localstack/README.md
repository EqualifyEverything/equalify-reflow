# LocalStack Configuration

This directory contains LocalStack initialization scripts and configuration for local AWS service emulation.

## Files

- `init-aws.sh` - Automatic initialization script that runs when LocalStack starts
  - Creates S3 buckets: `equalify-pdf-temp`, `equalify-pdf-results`
  - Configures bucket policies and CORS
  - Enables versioning on results bucket
  - Sets lifecycle policies for automatic cleanup

## LocalStack Services

The following AWS services are emulated locally:

- **S3** - Object storage for PDFs and HTML results
- **CloudWatch** - Logging and metrics (for future implementation)

## Accessing LocalStack

### AWS CLI (awslocal)

LocalStack provides the `awslocal` wrapper for AWS CLI:

```bash
# List S3 buckets
awslocal s3 ls

# List objects in a bucket
awslocal s3 ls s3://equalify-pdf-temp/

# Copy file to S3
awslocal s3 cp myfile.pdf s3://equalify-pdf-temp/

# Get object from S3
awslocal s3 cp s3://equalify-pdf-temp/myfile.pdf ./
```

### Python Boto3

Configure boto3 to use LocalStack endpoint:

```python
import boto3

s3_client = boto3.client(
    's3',
    endpoint_url='http://localhost:4566',
    aws_access_key_id='test',
    aws_secret_access_key='test',
    region_name='us-east-1'
)

# List buckets
response = s3_client.list_buckets()
print(response['Buckets'])
```

### Direct HTTP API

LocalStack exposes AWS APIs via HTTP:

```bash
# Check health
curl http://localhost:4566/_localstack/health

# List S3 buckets
curl http://localhost:4566/

# Access S3 object
curl http://localhost:4566/equalify-pdf-temp/test.txt
```

## Persistence

LocalStack data is persisted in the Docker volume `equalify-reflow-localstack-data`. To reset LocalStack:

```bash
# Stop and remove containers
docker-compose -f docker-compose.yml -f docker-compose.dev.yml down

# Remove LocalStack volume
docker volume rm equalify-reflow-localstack-data

# Restart (will re-initialize)
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

## Troubleshooting

### Buckets not created

Check LocalStack logs:
```bash
docker logs equalify-reflow-localstack
```

### Connection refused

Ensure LocalStack is running:
```bash
docker ps | grep localstack
curl http://localhost:4566/_localstack/health
```

### Script not executing

The init script runs automatically via LocalStack's `ready.d` hook. If it doesn't run:

1. Check script is executable: `ls -l infrastructure/localstack/init-aws.sh`
2. Check script is mounted: `docker exec equalify-reflow-localstack ls -l /etc/localstack/init/ready.d/`
3. Manually run: `docker exec equalify-reflow-localstack /etc/localstack/init/ready.d/init-aws.sh`