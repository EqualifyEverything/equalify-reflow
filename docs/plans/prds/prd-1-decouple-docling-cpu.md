# PRD-1: Decouple Docling-Serve from API Task (CPU, Always-On)

**Parent proposal:** `docs/plans/docling-gpu-scale-to-zero.md` (Phase 1, Section 7)
**Status:** Draft
**Dependencies:** None (first phase)
**Starting point:** Uncommitted changes on `main` from the batch processing debug session (see "Existing Work" below)

---

## 1. Goal

Split docling-serve out of the API Fargate task into its own ECS service running on CPU ARM Spot behind an internal ALB, with `min_capacity=1` (always-on), and layer cold-start handling on top of the existing retry/health-poll logic.

## 2. Background

The current architecture runs docling-serve as a sidecar container inside the API Fargate task (Section 2). This couples the API lifecycle to docling, forces shared memory (4 GB), and prevents independent scaling. This phase performs the architectural split described in Section 3 using a CPU image (Option C from Section 5) to validate the decoupled architecture before introducing GPU complexity in PRD-2.

### Existing work (uncommitted on main)

A batch processing debug session already applied several changes to `docling_serve_client.py` that partially address the proposal's Section 6.1 requirements. These changes are uncommitted on `main` and must be committed as a prerequisite to this PRD:

| Change | Status | File |
|--------|--------|------|
| Circuit breaker removed (was causing death spiral with shared-memory OOM) | Done | `src/services/docling_serve_client.py` |
| Semaphore added (`DOCLING_MAX_CONCURRENT`, default 2) | Done | `src/services/docling_serve_client.py` |
| `RemoteProtocolError` handler with `_wait_for_healthy(120s)` | Done | `src/services/docling_serve_client.py` |
| `_wait_for_healthy` method (polls `/health` every 3s) | Done | `src/services/docling_serve_client.py` |
| 504 carve-out (no retry for document-too-slow) | Done | `src/services/docling_serve_client.py` |
| Client timeout extended to 300s | Done | `src/services/docling_serve_client.py` |
| Noisy third-party loggers silenced | Done | `src/main.py` |
| Docling memory bumped to 8 GB, `MAX_SYNC_WAIT=600` | Done | `docker-compose.dev.yml` |

**Step 0 (prerequisite):** Commit these uncommitted changes to `main` before starting PRD-1 work.

### Circuit breaker decision

The proposal (Section 6.1) assumes the circuit breaker exists and extends it. The debug session removed it because it caused a death spiral in the sidecar context — one OOM killed everything. **This PRD re-introduces the circuit breaker with extended parameters** because the root cause (shared 4 GB memory) goes away with decoupling. When docling is a remote service behind an ALB, the circuit breaker serves its intended purpose: stop hammering a service that's genuinely down, without cascading failures from shared resources.

## 3. Requirements

### Infrastructure

- The system MUST deploy docling-serve as a standalone ECS service on EC2, separate from the API Fargate task.
- The system MUST use `c6g.xlarge` ARM Spot instances (4 vCPU, 8 GB) for the docling service.
- The system MUST have the `docling-serve-cpu:latest` image pushed to ECR (not pulled from quay.io at runtime).
- The system MUST create an internal ALB routing traffic to docling on port 5001.
- The system MUST return HTTP 503 from the ALB when no healthy docling targets exist.
- The system MUST configure the API task's `DOCLING_SERVE_URL` environment variable to point to the internal ALB DNS name on port 5001.
- The system MUST set `min_capacity=1` on the auto scaling target (always-on, no cold start in this phase).
- The system MUST set `max_capacity=2` on the auto scaling target.
- The system MUST pin the ECS-optimized AMI via a `data.aws_ami` data source with explicit filters, NOT using `$Latest`.
- The system MUST configure the docling security group to allow ingress on port 5001 from API task security group only, and egress on 443 only.
- The system MUST configure the docling ALB security group to allow ingress on 5001 from the VPC CIDR.
- The system MUST set `lifecycle { ignore_changes = [desired_count] }` on the ECS service to prevent Terraform from fighting auto scaling.
- The system MUST set `requires_compatibilities = ["EC2"]` on the docling task definition.

### Application Code — New Work (delta from uncommitted main)

- The system MUST re-introduce the circuit breaker with `failure_threshold=10` and `timeout=360s` (Section 6.1, item 1). The breaker was removed during the debug session; it is appropriate for a remote service behind an ALB where the OOM death spiral root cause no longer exists.
- The system MUST add 503-aware retry logic in `_convert_with_retry` that triggers `_wait_for_healthy` when the ALB returns 503 (Section 6.1, item 2). This is a new code path — the existing 5xx retry uses short exponential backoff, but 503 from an ALB with no targets needs the long health-poll.
- The system MUST extend `_wait_for_healthy` timeout from 120s to 300s (Section 6.1, item 3). The existing implementation polls every 3s up to 120s; this must cover the 2-3 minute cold start window.
- The system MUST add `httpx.ConnectError` to the health-poll path alongside `httpx.RemoteProtocolError` (Section 6.1, item 4). Currently only `RemoteProtocolError` triggers `_wait_for_healthy`; `ConnectError` falls through to short backoff retry.
- The system MUST publish a `JobsAwaitingProcessing` CloudWatch metric on document submission to pre-warm docling (Section 6.2). This MUST be best-effort (fire-and-forget, exception swallowed).
- The system MUST track `jobs_in_processing` as a Redis counter, incremented at the start of `process_document()` and decremented in a `finally` block (Section 6.3).
- The system MUST publish the `jobs_in_processing` counter to CloudWatch every 60s from the timeout worker (Section 6.3).
- The system MUST use `jobs_in_processing` (not PII queue depth) as the scaling signal for the docling service (Section 6.3).

### Application Code — Already Done (keep as-is from uncommitted main)

- Semaphore (`_DOCLING_SEMAPHORE`) limiting concurrent docling calls — keep, still needed.
- `RemoteProtocolError` handler with `_wait_for_healthy` — keep, extend timeout.
- 504 carve-out (no retry) — keep, good behavior.
- Client timeout at 300s — keep, matches `DOCLING_SERVE_MAX_SYNC_WAIT=600`.
- Noisy logger silencing — keep.

### Migration

- The system MUST support a zero-downtime migration path: deploy the new service with `min_capacity=1`, update `DOCLING_SERVE_URL`, verify, then remove the sidecar (Section 9, steps 1-5).
- The system MUST allow rollback by reverting `DOCLING_SERVE_URL` to `http://docling-serve:5001` while the Docker sidecar is still running (Section 9).

## 4. Out of Scope

- GPU instances or CUDA images (PRD-2)
- Scale-to-zero (`min_capacity=0`) (PRD-3)
- Spot instance pricing for g4dn instances (PRD-4)
- Processing queue with at-least-once delivery (Section 12, item 1 — tracked separately)
- Docling result checkpointing (Section 12, item 3 — tracked separately)

## 5. Code Changes

### Step 0: Commit existing uncommitted work

Before any PRD-1 work, commit the uncommitted changes on `main`:
- `src/services/docling_serve_client.py` — semaphore, retry rewrite, health-poll, circuit breaker removal
- `src/main.py` — noisy logger silencing
- `docker-compose.dev.yml` — docling 8 GB memory, `MAX_SYNC_WAIT=600`
- `src/services/rate_limit_service.py` — any pending fixes

### Modified Files

| File | Changes (delta from uncommitted main) |
|------|---------------------------------------|
| `src/services/docling_serve_client.py` | **Re-introduce circuit breaker** with extended params (threshold=10, timeout=360s). Add `_CIRCUIT_BREAKER.check_state()` in `convert()` before semaphore. Record success/failure in retry loop. **Add 503-aware branch** in `_convert_with_retry`: when `HTTPStatusError` with status 503, log cold-start warning, call `_wait_for_healthy(300)`, continue retry loop. **Extend `_wait_for_healthy`** default timeout from 120s to 300s. **Add `ConnectError` to health-poll path**: change the `RemoteProtocolError` handler to also catch `ConnectError` and route to `_wait_for_healthy`. Keep existing semaphore, 504 carve-out, 300s client timeout. |
| `src/api/documents.py` | Add CloudWatch `put_metric_data` call after job creation in `submit_document()` to publish `JobsAwaitingProcessing` metric. Best-effort, wrapped in try/except. Only in production (`settings.environment == "production"`). (Section 6.2) |
| `src/services/document_processing_service.py` | Add `redis.incr("eq-pdf:metrics:jobs_in_processing")` at start of `process_document()`, `redis.decr()` in `finally` block. (Section 6.3) |
| `src/workers/timeout_worker.py` | Add new scheduled task (every 60s): read `eq-pdf:metrics:jobs_in_processing` from Redis, publish to CloudWatch as `JobsInProcessing` metric. (Section 6.3) |

### New Files

| File | Purpose |
|------|---------|
| `terraform/docling.tf` | All Terraform resources for the docling ECS service (see Infrastructure Changes below) |

### Optional Improvement (Section 12, item 2)

| File | Changes |
|------|---------|
| `src/services/document_processing_service.py` | Move `storage.download_temp_file()` call to AFTER semaphore acquisition to prevent memory spikes during burst. This naturally fits with the `jobs_in_processing` counter changes. Note: with decoupling, the OOM risk is lower (API has full 4 GB to itself), but this is still good practice. |

## 6. Infrastructure Changes

All resources go in `terraform/docling.tf` unless otherwise noted.

| Resource | Type | Purpose |
|----------|------|---------|
| `data.aws_ami.ecs_optimized_arm` | Data source | Pinned ECS-optimized ARM AMI for c6g instances |
| `aws_iam_role.ecs_instance_role` | IAM role | EC2 instance role with `AmazonEC2ContainerServiceforEC2Role` policy |
| `aws_iam_instance_profile.ecs_instance` | IAM instance profile | Attach role to EC2 instances |
| `aws_launch_template.docling` | Launch template | c6g.xlarge, ARM AMI, ECS cluster user data, 30 GB gp3 root volume |
| `aws_autoscaling_group.docling` | ASG | min=0, max=2 (scaling target controls effective min), Spot mixed instances |
| `aws_ecs_capacity_provider.docling` | Capacity provider | Managed scaling, managed termination protection |
| `aws_ecs_cluster_capacity_providers.main` | Cluster attachment | Attach docling capacity provider to existing ECS cluster (modify existing resource if present in `ecs.tf`) |
| `aws_ecs_task_definition.docling` | Task definition | `docling-serve-cpu` container, port 5001, `requires_compatibilities = ["EC2"]`, 3584 MB memory, 4096 CPU |
| `aws_ecs_service.docling` | ECS service | Runs on docling capacity provider, registers with ALB target group, `lifecycle { ignore_changes = [desired_count] }` |
| `aws_lb.docling_internal` | Internal ALB | Internal load balancer in private subnets |
| `aws_lb_target_group.docling` | Target group | Port 5001, health check on docling health endpoint |
| `aws_lb_listener.docling` | Listener | Forward port 5001 to target group |
| `aws_security_group.docling` | Security group | Ingress 5001 from API SG, egress 443 only |
| `aws_security_group.docling_alb` | Security group | Ingress 5001 from VPC CIDR, egress to docling SG |
| `aws_appautoscaling_target.docling` | Scaling target | `min_capacity=1`, `max_capacity=2` (min changes to 0 in PRD-3) |
| `aws_appautoscaling_policy.docling` | Scaling policy | Target tracking on `jobs_in_processing` CloudWatch metric |
| `aws_cloudwatch_metric_alarm.docling_scale` | Alarm | Trigger scaling when `JobsAwaitingProcessing > 0` or `jobs_in_processing > 0` |

### Existing Terraform modifications

| File | Changes |
|------|---------|
| `terraform/ecs.tf` | Remove docling-serve sidecar container from API task definition. Update `DOCLING_SERVE_URL` env var to point to internal ALB DNS. (Do this AFTER verifying the decoupled service works.) |

## 7. Verification

1. **Deploy and verify connectivity:**
   - Deploy Terraform. Confirm docling ECS service has 1 running task.
   - Confirm internal ALB health check passes on docling target group.
   - Confirm API task can reach docling via ALB URL.

2. **Run batch test:**
   ```
   uv run scripts/batch_run.py
   ```
   - All 31 test documents MUST process successfully.
   - Processing times should be comparable to current sidecar performance (~90s/doc average on CPU).

3. **Verify metrics:**
   - `jobs_in_processing` CloudWatch metric publishes correctly (check CloudWatch console).
   - `JobsAwaitingProcessing` metric fires on document submission.
   - Circuit breaker does NOT open during normal operation.

4. **Verify isolation:**
   - Heavy docling processing does NOT degrade API response times for `GET /api/v1/documents/{job_id}`.
   - API task memory usage is lower than before (no docling sidecar competing for 4 GB).

5. **Verify rollback:**
   - Revert `DOCLING_SERVE_URL` to `http://docling-serve:5001` (with sidecar still in task definition) and confirm documents still process.

## 8. Risks

| Risk | Source | Mitigation |
|------|--------|------------|
| Circuit breaker re-introduction causes regressions | New (debug session removed it for good reason) | The death spiral root cause was shared 4 GB memory. Decoupling eliminates this. Extended params (threshold=10, timeout=360s) give far more headroom than the original (3, 30s). If issues recur, remove breaker again — the retry + health-poll logic works standalone. |
| Circuit breaker opens during transient ALB issues | Section 8, row 1 | 503-aware retry triggers `_wait_for_healthy(300s)` instead of short backoff. Threshold of 10 prevents premature opening. |
| Premature scale-in while jobs processing | Section 8, row 5 | `jobs_in_processing` counter (Section 6.3) used as scaling signal instead of queue depth. Not critical in Phase 1 (min=1, always-on) but code must be in place for Phase 3. |
| In-flight BackgroundTask lost on API task recycle | Section 8, row 7 / Section 12, item 1 | Pre-existing issue, not introduced by this phase. Tracked separately. |
| 100-doc burst causes API task OOM | Section 8, row 8 / Section 12, item 2 | Less severe with decoupling (API gets full 4 GB). Optionally addressed by moving PDF download inside semaphore (see optional improvement). |
| Mixed AMI versions in ASG | Section 8, row 10 | AMI pinned via data source filter, not `$Latest`. |
| Stuck EC2 instances with scale-in protection | Section 8, row 6 | Monitor instance count vs task count. Manual remediation if needed. Not critical in Phase 1 (always-on). |

## 9. Rollback Plan

From Section 9:

1. Revert `DOCLING_SERVE_URL` environment variable to `http://docling-serve:5001` (Docker service name).
2. Re-add docling-serve sidecar to API task definition (if already removed).
3. Deploy the reverted API task definition.
4. Leave the docling ECS service running (harmless) or tear down by removing resources from `docling.tf`.

The sidecar should NOT be removed from the API task definition until the decoupled service is verified working in production for at least 24 hours.

**Code rollback:** The circuit breaker re-introduction, 503 handling, and metric publishing are all additive and backward-compatible. They work with both the sidecar (localhost) and decoupled (ALB) architectures. No code rollback needed when reverting the infrastructure.
