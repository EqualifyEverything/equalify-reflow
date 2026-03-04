# PRD-3: Scale-to-Zero

**Parent proposal:** `docs/plans/docling-gpu-scale-to-zero.md` (Phase 3, Section 7)
**Status:** Draft
**Dependencies:** PRD-2 complete (GPU acceleration working on g4dn.xlarge On-Demand)

---

## 1. Goal

Enable scale-to-zero for the docling GPU service by setting `min_capacity=0`, validating cold start behavior end-to-end, and confirming the pre-warm trigger reduces perceived latency.

## 2. Background

PRD-1 deployed the decoupled architecture with all code changes (circuit breaker extensions, pre-warm metric, `jobs_in_processing` counter). PRD-2 swapped to GPU instances. Both phases ran with `min_capacity=1` (always-on). This phase enables the core cost optimization: the GPU instance runs only when documents are being processed. With ~1.5 hrs/day of actual compute needed (Section 4), scale-to-zero drops GPU cost from ~$379/mo (always-on On-Demand) to ~$24/mo (Section 5, Option B).

Cold start is 2-3 minutes (instance launch 90s + model load 120s, per Section 3). The code changes from PRD-1 (503-aware retry, 300s health poll, 360s circuit breaker timeout) are designed to bridge this gap. The pre-warm trigger (Section 6.2) overlaps cold start with PII scanning.

## 3. Requirements

- The system MUST set `min_capacity=0` on the docling auto scaling target.
- The system MUST scale up from zero when the `JobsAwaitingProcessing` or `jobs_in_processing` CloudWatch metric indicates work is queued.
- The system MUST survive a cold start without the circuit breaker opening. The 503-aware retry (PRD-1, Section 6.1) and 300s `_wait_for_healthy` timeout MUST bridge the 2-3 minute cold start window.
- The system MUST complete a document submitted from cold (zero instances) within 5 minutes total (cold start + processing).
- The system MUST scale back to zero when `jobs_in_processing` = 0 AND no jobs are in the PII queue. Scale-in cooldown MUST be at least 300s to avoid thrashing.
- The system MUST NOT scale in while `jobs_in_processing` > 0 (Section 6.3).
- The pre-warm trigger (Section 6.2) MUST fire on document submission, overlapping cold start with PII scan time.
- The CloudWatch alarm evaluating the scaling metric MUST have a period short enough to trigger scale-out within 60s of a job submission (1-minute evaluation period).

## 4. Out of Scope

- Spot pricing (PRD-4)
- Application code changes (all completed in PRD-1)
- Instance type or image changes (completed in PRD-2)

## 5. Code Changes

No application code changes required. All cold-start handling was implemented in PRD-1.

### Modified Files

| File | Changes |
|------|---------|
| `terraform/docling.tf` | See Infrastructure Changes below |

## 6. Infrastructure Changes

All changes in `terraform/docling.tf`:

| Resource | Change |
|----------|--------|
| `aws_appautoscaling_target.docling` | Change `min_capacity` from `1` to `0`. |
| `aws_appautoscaling_policy.docling` | Verify scale-in cooldown is >= 300s. Verify target value triggers scale-out when metric > 0. |
| `aws_cloudwatch_metric_alarm.docling_scale` | Verify evaluation period is 60s (1 minute). Verify alarm triggers on `JobsAwaitingProcessing >= 1` OR `jobs_in_processing >= 1`. Ensure `treat_missing_data = "notBreaching"` so silence doesn't trigger false scale-outs. |

### Tuning parameters to validate

These are not new resources but settings that must be confirmed correct for scale-to-zero:

- **ECS capacity provider `managed_scaling`**: `minimum_scaling_step_size = 1`, `maximum_scaling_step_size = 1`, `target_capacity = 100`.
- **ASG `min_size`**: Must be `0` (the capacity provider manages effective minimums).
- **Scale-in protection**: ECS managed termination protection must be enabled so tasks aren't killed mid-processing.

## 7. Verification

1. **Confirm zero state:**
   - After `terraform apply`, wait for scale-in cooldown to expire.
   - Confirm ASG desired count = 0, ECS service desired count = 0.
   - Confirm internal ALB target group shows 0 healthy targets.

2. **Cold start test:**
   - Submit a single document via the API when docling is at zero instances.
   - Observe:
     - `JobsAwaitingProcessing` metric publishes immediately.
     - CloudWatch alarm transitions to ALARM within 60s.
     - ASG launches a g4dn.xlarge instance.
     - ECS places the docling task on the instance.
     - ALB target becomes healthy.
     - DoclingServeClient `_wait_for_healthy` polls until ALB returns 200.
     - Document processes successfully.
   - Total time from submission to completion MUST be < 5 minutes.
   - Circuit breaker MUST NOT open.

3. **Scale-in test:**
   - After the document completes, verify `jobs_in_processing` drops to 0.
   - Wait for scale-in cooldown (300s+).
   - Confirm ASG scales back to 0 instances.

4. **Batch test from cold:**
   ```
   uv run scripts/batch_run.py
   ```
   - Submit batch when docling is at zero.
   - First document may take ~3-5 min (cold start). Subsequent documents should process at GPU speed (~15-25s).
   - All 31 documents MUST complete successfully.

5. **Monitor for 1 week:**
   - Check for stuck instances (instance count > 0 when no jobs processing).
   - Check for circuit breaker events in application logs.
   - Check for failed jobs caused by cold start timeouts.
   - Check daily cost — should be ~$0.526/hr x actual hours, trending toward $24/mo.

## 8. Risks

| Risk | Source | Mitigation |
|------|--------|------------|
| Circuit breaker opens during cold start | Section 8, row 1 | Code changes from PRD-1 (503-aware retry, 300s health poll, failure_threshold=10, timeout=360s) are designed for this scenario. If cold start exceeds 300s, increase `_wait_for_healthy` timeout. |
| Cold start too slow for user experience | Section 8, row 4 | Pre-warm on submit (Section 6.2) overlaps cold start with PII scanning (15-30s). Worst case adds ~2 min to first-job latency. Acceptable for batch processing; communicate expected latency to users. |
| Premature scale-in while jobs processing | Section 8, row 5 | `jobs_in_processing` counter (Section 6.3) prevents scale-in during active processing. Scale-in cooldown >= 300s. |
| Stuck EC2 instances with scale-in protection | Section 8, row 6 | Monitor instance count vs task count via CloudWatch. Manual remediation via ASG console if stuck. More visible at min=0 because stuck instances mean nonzero cost when expected is zero. |
| CloudWatch alarm evaluation delay | N/A | If alarm evaluation period > 60s, cold start perception worsens. Ensure 60s period with 1 evaluation period before triggering. |

## 9. Rollback Plan

1. Set `min_capacity=1` on `aws_appautoscaling_target.docling` in Terraform.
2. `terraform apply` — ASG will launch an instance immediately.
3. System returns to always-on GPU behavior (PRD-2 state).
4. Cost returns to ~$379/mo On-Demand until PRD-4 (Spot) is applied.

This is a safe, instant rollback. No code changes required.
