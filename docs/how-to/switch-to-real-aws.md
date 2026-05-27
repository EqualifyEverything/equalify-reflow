# Switching the dev stack from Floci to real AWS

The local dev stack defaults to **Floci** (an S3-compatible local emulator)
for S3 and CloudWatch. Bedrock always points at real AWS because no local
emulator covers it. This document covers what to do when you want to flip
S3 + CloudWatch to real AWS too — typically for staging-like validation,
or to test against a real bucket before deploying to Fargate.

## When you actually need this

- You're validating the end-to-end pipeline against a real S3 bucket
  (e.g., to confirm bucket-level lifecycle rules apply correctly).
- You're testing IAM-role-based auth instead of long-lived access keys.
- You're debugging a presigned-URL bug that only shows up on real AWS.

If you're just developing features, **don't switch.** Floci is faster,
free, and doesn't risk leaking PII to a production bucket.

## Prerequisites

You need an AWS account and an IAM user (or role) with these permissions:

- `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` on the
  Reflow buckets (defaults: `equalify-pdf-temp`, `equalify-pdf-results`).
- `cloudwatch:PutMetricData` if you want metrics export.
- `bedrock:InvokeModel` if you're using AWS Bedrock for AI agents (vs.
  Anthropic direct, which doesn't touch AWS).

The buckets must exist before Reflow starts — boto3 won't create them.
Easiest path: log into the AWS console once and create
`equalify-pdf-temp-<your-initials>` and `equalify-pdf-results-<your-initials>`
in the same region as your Bedrock endpoint (`us-east-1` is the default).

## Steps

### 1. Get an access key id + secret access key

Console → IAM → Users → pick your IAM user → **Security credentials**
tab → **Create access key**. Choose "Command Line Interface (CLI)" as
the use case. Download the CSV; you'll only see the secret once.

### 2. Put the credentials in your local `.env`

Edit `.env` in the repo root (create it if absent):

```
AWS_ACCESS_KEY_ID=AKIA...        # your real key id
AWS_SECRET_ACCESS_KEY=...        # your real secret
AWS_DEFAULT_REGION=us-east-1     # or whichever region your buckets live in
S3_TEMP_BUCKET=equalify-pdf-temp-<your-initials>
S3_RESULTS_BUCKET=equalify-pdf-results-<your-initials>
```

These override the floci defaults defined in `docker-compose.dev.yml`
(which pull from the same env vars but fall back to `test`).

### 3. Remove the Floci endpoint override

In `docker-compose.dev.yml`, comment out the line:

```yaml
- AWS_ENDPOINT_URL_S3=http://floci:4566
```

This is what tells boto3 to talk to floci. With it gone, boto3 uses the
real AWS S3 endpoint determined by the region. Leave
`AWS_ENDPOINT_URL_CLOUDWATCH` either commented or pointing at real AWS
the same way.

### 4. Update the public/internal S3 URL settings

Real AWS doesn't need separate public/internal URLs — both are
`https://s3.<region>.amazonaws.com` (or virtual-hosted-style
`https://<bucket>.s3.<region>.amazonaws.com`). Comment these out so the
rewrite helper becomes a no-op:

```yaml
# - S3_PUBLIC_URL=http://localhost:4566
# - S3_INTERNAL_URL=http://floci:4566
```

### 5. Restart the stack

```powershell
docker compose up -d --force-recreate api-gateway
docker compose logs --tail=30 api-gateway --follow
```

You should see normal startup with no `ProfileNotFound` errors. Hit
`/health` to confirm the S3 check passes against the real bucket:

```powershell
curl https://etsuko-unmurmurous-unneurotically.ngrok-free.dev/health
```

A `200 OK` with `s3: true` in the JSON means real S3 is responding.

## Rollback to Floci

Reverse every step in section "Steps":

- Uncomment `AWS_ENDPOINT_URL_S3=http://floci:4566`.
- Uncomment `S3_PUBLIC_URL` and `S3_INTERNAL_URL`.
- Restore `S3_TEMP_BUCKET=equalify-pdf-temp` and `S3_RESULTS_BUCKET=equalify-pdf-results`.
- In your `.env`, set `AWS_ACCESS_KEY_ID=test` and `AWS_SECRET_ACCESS_KEY=test`
  (or delete the lines — the compose default will provide `test`).
- Restart.

## Common gotchas

- **`ProfileNotFound: default`.** Means `AWS_PROFILE` is set to an empty
  string somewhere. boto3 reads `""` as "use profile 'default'" and then
  tries `~/.aws/credentials`. Fix: leave `AWS_PROFILE` *completely
  unset* (don't even define the env key). The compose file already
  follows this rule; if you re-add it, comment it out instead.
- **`AccessDenied` on PutObject.** IAM user lacks `s3:PutObject` on the
  bucket. Don't broaden IAM to `*` — scope it to the two Reflow bucket
  ARNs.
- **Presigned URLs return 403.** Bucket has a "block all public access"
  setting at the bucket level. Either turn that off (relaxes security)
  or configure the bucket policy to allow GET on presigned-URL paths.
- **`InvalidLocationConstraint`.** Your bucket is in a different region
  than `AWS_DEFAULT_REGION`. Either move the bucket or update the env.

## Production path

Once Reflow is on Fargate, you should NOT use long-lived access keys.
Instead the ECS task runs under an IAM task role and boto3 picks up
credentials automatically via the EC2/ECS metadata service. The compose
flag `AWS_EC2_METADATA_DISABLED=true` exists only because the local
container has no such metadata service; in Fargate, you'll want that
unset (or removed) so the task role chain works.

See `infrastructure/fargate/main.tf` for the Terraform module that wires
up the IAM task role at deploy time.
