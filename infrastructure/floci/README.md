# Floci Configuration

This directory contains the Floci initialization script and configuration for local AWS service emulation. Floci replaces LocalStack as the dev/CI emulator — same wire protocol, same port (4566), ~72 MB image, ~26 ms startup, MIT licensed, no auth gates.

Floci upstream: <https://github.com/floci-io/floci>

## Files

- `init-aws.sh` — Sidecar initialization script that runs once after Floci reports healthy.
  - Creates S3 buckets: `equalify-pdf-temp`, `equalify-pdf-results`
  - Configures CORS and public-read bucket policies
  - Enables versioning on the results bucket
  - Sets a 7-day lifecycle policy on the temp bucket

The script is invoked from a companion `floci-init` service in `docker-compose.dev.yml` / `docker-compose.ci.yml` using the `amazon/aws-cli` image, with `AWS_ENDPOINT_URL=http://floci:4566` in the environment so every `aws` command automatically targets Floci.

## Floci services

Floci enables a superset of AWS services by default (s3, monitoring, logs, sqs, sns, lambda, dynamodb, ssm, etc.). The app only uses S3 in dev and CI; the CloudWatch endpoint is kept pointed at Floci as an inert safety rail for a few prod-only code paths that are gated behind `settings.environment == "production"`.

## Accessing Floci

### AWS CLI

Unlike LocalStack, Floci does not ship an `awslocal` wrapper. Use the standard `aws` CLI and point it at Floci via `AWS_ENDPOINT_URL`:

```bash
export AWS_ENDPOINT_URL=http://localhost:4566
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1

aws s3 ls
aws s3 ls s3://equalify-pdf-temp/
aws s3 cp myfile.pdf s3://equalify-pdf-temp/
aws s3 cp s3://equalify-pdf-temp/myfile.pdf ./
```

Or use the `make floci-debug` helper for a cheat-sheet.

### Python boto3

Configure boto3 to target the Floci endpoint — identical pattern to LocalStack:

```python
import boto3

s3_client = boto3.client(
    "s3",
    endpoint_url="http://localhost:4566",
    aws_access_key_id="test",
    aws_secret_access_key="test",
    region_name="us-east-1",
)

print(s3_client.list_buckets()["Buckets"])
```

The app doesn't hardcode any of this — `boto3` reads `AWS_ENDPOINT_URL_S3` from the environment automatically, so nothing in `src/` knows whether it's talking to Floci or real AWS.

### Direct HTTP API

```bash
# Healthcheck — root returns 200 with ListAllMyBucketsResult XML
curl http://localhost:4566/

# Access an object (path-style URL, what S3_PUBLIC_URL builds)
curl http://localhost:4566/equalify-pdf-temp/test.txt
```

Floci does not expose a dedicated `/health` endpoint (LocalStack's `/_localstack/health` has no equivalent). The Docker Compose healthcheck uses `GET /` instead, which is cheap and deterministic.

## Persistence

In `docker-compose.dev.yml` Floci runs with `FLOCI_STORAGE_MODE=persistent` and persists to the named volume `equalify-reflow-floci-data`. Buckets and objects survive `docker compose restart floci` and `docker compose down && docker compose up`.

To reset Floci state:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
docker volume rm equalify-reflow-floci-data
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

`docker-compose.ci.yml` uses the default in-memory storage mode — CI doesn't need persistence between runs, and skipping the volume shaves a few hundred milliseconds.

## Troubleshooting

### Buckets not created

The buckets are created by the `floci-init` sidecar, not by Floci itself. Check its logs:

```bash
docker logs equalify-reflow-floci-init
```

You should see `✓ Bucket equalify-pdf-temp created successfully` and friends. If the sidecar isn't running, check that Floci reported healthy — the sidecar's `depends_on` gate blocks it from starting otherwise:

```bash
docker logs equalify-reflow-floci
docker inspect equalify-reflow-floci --format '{{.State.Health.Status}}'
```

### Connection refused

```bash
docker ps | grep floci
curl http://localhost:4566/
```

### Script not executing

Unlike LocalStack's `ready.d` hook, Floci has no built-in init hook — initialization is handled by the external `floci-init` sidecar. If the sidecar exited before the buckets were created:

1. Check exit code: `docker inspect equalify-reflow-floci-init --format '{{.State.ExitCode}}'`
2. Re-run it manually: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up floci-init`
