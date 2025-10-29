# Environment Setup - Simplified Guide

## TL;DR

**Local Development:**
```bash
make dev          # Everything just works
```

**AWS Operations:**
```bash
# One-time setup
cp .aws-config-example ~/.aws/config  # Merge with existing config
aws sso login --profile uic

# Then use Makefile commands (auto-login if token expires!)
make aws-health   # Check deployment
make aws-logs     # View logs
make aws-status   # ECS status
make aws-shell    # Connect to ECS container (requires session-manager-plugin)
```

---

## The Key Insight

Your project has **two separate worlds** that don't need to interact:

### 🏠 Local Development World
- **Docker Compose** runs everything
- **.env file** configures containers
- **LocalStack** is inside Docker network (`localstack:4566`)
- **You interact through:** `make dev`, `curl localhost:8080`, `make test`

### ☁️ AWS Production World
- **AWS CLI** manages real infrastructure
- **AWS Profiles** configure authentication
- **Real AWS** services in us-east-1
- **You interact through:** `make aws-*` commands or AWS Console

**These are separate workflows.** You don't "switch" between them - you use different tools.

---

## Setup: One-Time Configuration

### Step 1: AWS Profile Configuration

Copy the example to your AWS config:

```bash
cat .aws-config-example >> ~/.aws/config
```

This adds two profiles:
- `uic` - Real AWS (your production account)
- `localstack` - LocalStack (debugging only, rarely needed)

### Step 2: AWS SSO Login

```bash
aws sso login --profile uic
```

That's it. Now all AWS commands work.

---

## Daily Workflows

### Local Development

```bash
# Start everything
make dev

# Test your changes
curl http://localhost:8080/health
make test-fast

# View logs
make logs-api

# Debug inside container
make shell

# Stop everything
make down
```

**LocalStack is running but you don't directly interact with it.** The app and tests handle it automatically.

### AWS Operations

```bash
# Check deployment health
make aws-health

# View live logs
make aws-logs

# Check ECS status
make aws-status

# Connect to ECS container (requires session-manager-plugin)
make aws-shell

# Deploy changes
make aws-deploy
```

**Auto-login:** All AWS commands automatically detect expired SSO tokens and prompt you to login. No need to manually check or run `aws sso login` first!

All AWS commands automatically use `AWS_PROFILE=uic` (set in Makefile).

---

## Why This Works

### The `.env` File (Don't Source It!)

Your `.env` is **only for Docker Compose**:

```bash
# .env - Docker Compose reads this automatically
AWS_ENDPOINT_URL=http://localstack:4566  # ← Docker DNS name
```

**Never run:** `source .env` in your shell ❌

The endpoint `localstack:4566` only works inside Docker. From your host, it's `localhost:4566`.

### AWS Profiles (The Right Way)

AWS CLI uses `~/.aws/config`:

```ini
[profile uic]
region = us-east-1
# No endpoint_url = uses real AWS

[profile localstack]
region = us-east-1
endpoint_url = http://localhost:4566  # ← Host networking
```

Commands use: `AWS_PROFILE=uic aws ...`

This is **AWS's native feature**, works on all platforms (Windows/Mac/Linux).

---

## Advanced: When You'd Use Each Profile

### `AWS_PROFILE=uic` (Most Common)

```bash
# Check ECS services
AWS_PROFILE=uic aws ecs describe-services ...

# View logs
AWS_PROFILE=uic aws logs tail /ecs/equalify-pdf --follow

# Manage S3
AWS_PROFILE=uic aws s3 ls s3://equalify-pdf-temp-380610849750/
```

**Or just use Makefile:** `make aws-logs`, `make aws-status`, etc.

### `AWS_PROFILE=localstack` (Rarely Needed)

Only for debugging LocalStack from your host machine:

```bash
# List LocalStack S3 buckets (from host)
AWS_PROFILE=localstack aws s3 ls

# Check what's in temp bucket
AWS_PROFILE=localstack aws s3 ls s3://equalify-pdf-temp/
```

**But usually you'd just:**
```bash
# Check via the API
curl http://localhost:8080/health

# Or exec into container
docker exec -it equalify-pdf-api-gateway bash
# Now you're inside Docker network, can use localstack:4566
```

---

## Troubleshooting

### "Could not connect to endpoint URL: http://localstack:4566"

**Problem:** You're trying to use AWS CLI from host with Docker DNS name.

**Solution:**
```bash
# If you need to debug LocalStack from host:
AWS_PROFILE=localstack aws s3 ls  # Uses localhost:4566

# Or just use the app:
curl http://localhost:8080/api/...
```

### "The security token is invalid"

**Problem:** AWS SSO session expired.

**Solution:**
```bash
aws sso login --profile uic
```

**Note:** All `make aws-*` commands now handle this automatically! If your token is expired, the Makefile will detect it, prompt you to login, and retry the command.

### "Which environment am I in?"

**You're not "in" an environment.** You're using different tools:

```bash
# Local development
make dev          # Docker world
curl localhost:8080

# AWS operations
make aws-health   # AWS world
```

They're separate workflows, not modes to switch between.

---

## Platform Support

This setup works identically on:
- ✅ **macOS** (native terminal)
- ✅ **Linux** (native terminal)
- ✅ **Windows** (WSL2 required)

**Windows users:** Install WSL2 + Docker Desktop, then follow Linux instructions.

---

## Summary: Best Practices

1. ✅ **Never source `.env`** - it's Docker-only
2. ✅ **Use Makefile commands** - they handle profiles automatically
3. ✅ **Local and AWS are separate workflows** - don't try to "switch"
4. ✅ **For manual AWS commands** - use `AWS_PROFILE=uic`
5. ✅ **Rarely need LocalStack profile** - app and tests handle it

**Most common mistake:** Trying to use `aws` CLI against LocalStack manually. You almost never need to - that's what your app and tests do.

---

**Last Updated:** 2025-10-15
