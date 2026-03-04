# GPU Deployment Decisions — Docling-Serve (PRD-2/3/4)

Deployed March 4, 2026. This document covers the rationale, costs, and operational details for the docling-serve GPU migration.

## Why GPU?

Docling-serve uses deep learning models (layout analysis, table structure recognition, OCR) to parse PDFs into structured data. On CPU (c6g.xlarge, 4 ARM cores), a 13-page PDF takes ~90 seconds. The same PDF on GPU (g4dn.xlarge, NVIDIA T4) takes ~15-25 seconds — roughly 4x faster. This matters because:

- Users wait for real-time processing via SSE streaming
- The API has a 300-second timeout; complex PDFs were hitting it on CPU
- Faster processing means the instance is idle sooner, which matters for scale-to-zero billing

## Why g4dn.xlarge?

| Instance | GPU | vCPUs | RAM | GPU RAM | Spot $/hr | On-Demand $/hr |
|----------|-----|-------|-----|---------|-----------|----------------|
| g4dn.xlarge | 1x T4 | 4 | 16 GB | 16 GB | ~$0.16 | ~$0.526 |
| g4dn.2xlarge | 1x T4 | 8 | 32 GB | 16 GB | ~$0.22 | ~$0.752 |
| g5.xlarge | 1x A10G | 4 | 16 GB | 24 GB | ~$0.36 | ~$1.006 |
| p3.2xlarge | 1x V100 | 8 | 61 GB | 16 GB | ~$0.92 | ~$3.06 |

g4dn.xlarge is the cheapest GPU instance that fits. The T4 has 16 GB VRAM — docling's models use ~4-6 GB, so there's headroom. The g4dn.2xlarge is configured as a fallback if xlarge Spot capacity is unavailable in the requested AZ. Going to g5 or p3 would be 2-6x more expensive with no meaningful benefit for this workload.

## Why Spot instances?

Spot instances are spare AWS capacity sold at 60-70% discount. The tradeoff is AWS can reclaim them with 2 minutes notice. This works for docling because:

- PDF processing is **idempotent** — if an instance is reclaimed mid-job, the application's circuit breaker detects the failure and the job retries automatically
- The ECS agent has `ECS_ENABLE_SPOT_INSTANCE_DRAINING=true`, which gracefully drains tasks when a Spot interruption notice arrives
- The ASG uses `capacity-optimized` allocation strategy, which picks the Spot pool least likely to be interrupted
- g4dn Spot interruption rates are historically low (<5% in us-east-1)

## Why scale-to-zero?

This is a low-traffic application — maybe 10-50 PDFs per day. A g4dn.xlarge running 24/7 costs ~$114/mo on Spot. With scale-to-zero, you only pay for actual processing time. At 50 PDFs/day averaging 30 seconds each, that's ~25 minutes of GPU time per day, or ~$7-9/mo.

The tradeoff is cold start latency. When the system is at zero instances and a PDF is submitted:

1. CloudWatch alarm fires on the `JobsInProcessing` metric (~60s evaluation)
2. ECS capacity provider scales the ASG from 0 to 1 (~30s to launch instance)
3. Docker pulls the CUDA image (~60-90s first time, cached after)
4. Docling loads ML models into GPU memory (~120-180s)
5. ALB health check passes, task registers as healthy

Total cold start: **3-5 minutes**. The application code handles this with a circuit breaker and retry logic that was built in PRD-1. Subsequent PDFs while the instance is warm process in 15-25 seconds.

Scale-in happens after 120 seconds of no jobs (the cooldown on the step scaling policy). ECS managed scaling handles the ASG lifecycle.

## Cost Comparison

| Configuration | Monthly Cost | Notes |
|---------------|-------------|-------|
| PRD-1: c6g.xlarge CPU, always-on, Spot | ~$38/mo | What we had before |
| g4dn.xlarge GPU, always-on, On-Demand | ~$379/mo | Worst case |
| g4dn.xlarge GPU, always-on, Spot | ~$114/mo | If we kept min_capacity=1 |
| g4dn.xlarge GPU, scale-to-zero, On-Demand | ~$24/mo | **Where we are now** (temporary) |
| g4dn.xlarge GPU, scale-to-zero, Spot | ~$7-9/mo | **Target state** |

The On-Demand estimate assumes ~25 min/day of usage. Spot estimate assumes the same usage at ~70% discount. Actual costs depend on traffic volume.

## Current State (Temporary)

We're running **scale-to-zero with On-Demand GPU** because the AWS account's Spot GPU quota is zero. This is a temporary state that costs ~$24/mo instead of the target ~$7-9/mo.

### Why we needed a quota increase

AWS applies per-account vCPU limits to each instance family. The "All G and VT Spot Instance Requests" quota controls how many vCPUs of GPU Spot instances you can run. New accounts (or accounts that have never used GPU Spot) start at 0. This is an AWS safety measure to prevent accidental large Spot bills.

We need 4 vCPUs (one g4dn.xlarge) minimum, and requested 8 (to allow the g4dn.2xlarge fallback). The On-Demand GPU quota was already at 384 vCPUs, which is why On-Demand works immediately.

### How to check on the quota approval

**Option 1 — AWS CLI:**
```bash
aws service-quotas get-requested-service-quota-change \
  --request-id 1065f90ed9e84f9c96147f8e09fd03e0Art0IrWl \
  --region us-east-1 \
  --query '{status:RequestedQuota.Status,desired:RequestedQuota.DesiredValue}'
```

**Option 2 — AWS Console:**
Support case: https://us-east-1.console.aws.amazon.com/support/home#/case/?displayId=177265998400600

**Option 3 — Check if quota changed:**
```bash
aws service-quotas list-service-quotas --service-code ec2 --region us-east-1 \
  --query 'Quotas[?QuotaCode==`L-3819A6DF`].{name:QuotaName,value:Value}'
```
When `value` changes from 0 to 8, it's approved.

### How to switch to Spot once approved

In `terraform/docling.tf`, change:
```hcl
on_demand_percentage_above_base_capacity = 100  # On-Demand until Spot quota approved
```
to:
```hcl
on_demand_percentage_above_base_capacity = 0  # 100% Spot
```
Then run `terraform apply`.

## Architecture Decisions

### Why ECR instead of pulling from quay.io directly?

The CUDA image is ~8-10 GB. Pulling from quay.io on every cold start would add 2-3 minutes to an already long cold start. ECR is in the same region (us-east-1) and pulls at ~1 GB/s over the VPC endpoint. After the first pull, Docker layer caching on the instance makes subsequent starts near-instant.

We also use `IMMUTABLE` tag mutability on the ECR repo. This means once `cu128-v1.14.2` is pushed, it can never be overwritten. Deployments are explicit version bumps, not mutable `:latest` tags.

### Why mixed instances policy instead of a single instance type?

The ASG uses a `mixed_instances_policy` with g4dn.xlarge (primary) and g4dn.2xlarge (fallback). If Spot capacity for xlarge is unavailable in one AZ, AWS can fulfill with 2xlarge instead. The `capacity-optimized` strategy picks whichever pool has the most available capacity, reducing interruption risk.

### Why deployment_minimum_healthy_percent = 0?

Standard ECS deployments require at least one healthy task before draining the old one (100% minimum). With scale-to-zero, there are often zero running tasks, so a new deployment would deadlock waiting for a healthy task that can never start. Setting this to 0 allows deployments to proceed from a cold state.

### Why treat_missing_data = "notBreaching" on the CloudWatch alarm?

The `JobsInProcessing` metric is only emitted when the API is handling jobs. When the system is idle, no data points are published. By default, CloudWatch treats missing data as "missing" which can trigger INSUFFICIENT_DATA state transitions. Setting `notBreaching` means silence = no jobs = everything is fine.

### Why 50 GB EBS instead of 30 GB?

The CUDA image is ~8-10 GB (vs ~2 GB for the CPU image). Docker needs roughly 2x the image size for pulling and extracting layers. Add the OS, ECS agent, and model cache, and 30 GB was tight. 50 GB gives comfortable headroom at negligible cost (~$4/mo for gp3).

## Rollback Options

| Scenario | What to change | Effect |
|----------|---------------|--------|
| Back to PRD-1 (ARM CPU) | Revert `docling.tf` + `variables.tf` | ARM Spot, always-on, ~90s processing |
| GPU without scale-to-zero | Set `docling_min_capacity = 1` in `variables.tf` | GPU always-on, no cold starts |
| GPU On-Demand (current) | `on_demand_percentage_above_base_capacity = 100` | No Spot quota needed |
| GPU Spot (target) | `on_demand_percentage_above_base_capacity = 0` | Requires Spot quota ≥ 4 vCPUs |

All changes are `terraform apply` — no application code changes required.

## Key Files

- `terraform/docling.tf` — All docling infrastructure (AMI, launch template, ASG, task def, service, scaling)
- `terraform/variables.tf` — Instance type, min capacity, image tag defaults
- `terraform/ecr.tf` — Docling ECR repository and lifecycle policy
- `terraform/outputs.tf` — ECR URL output
