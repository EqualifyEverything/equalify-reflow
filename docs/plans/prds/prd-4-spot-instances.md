# PRD-4: Spot Instances

**Parent proposal:** `docs/plans/docling-gpu-scale-to-zero.md` (Phase 4, Section 7)
**Status:** Draft
**Dependencies:** PRD-3 complete (scale-to-zero working on GPU On-Demand)

---

## 1. Goal

Switch the docling GPU instances from On-Demand to Spot pricing, reducing GPU compute cost from ~$0.526/hr to ~$0.16-0.19/hr (~65-70% savings), and validate the system handles Spot interruptions gracefully.

## 2. Background

PRDs 1-3 established the decoupled, GPU-accelerated, scale-to-zero architecture. The system currently uses On-Demand g4dn.xlarge at ~$24/mo (1.5 hrs/day, Section 5 Option B). Switching to Spot drops this to ~$7-9/mo (Section 5, Option A). The existing retry logic and circuit breaker (PRD-1) already handle mid-connection drops from instance termination — the same failure mode as Spot interruptions. This phase validates that assumption.

This is the final phase and is marked optional in the proposal (Section 7, Phase 4).

## 3. Requirements

- The system MUST use Spot pricing for g4dn.xlarge instances.
- The system MUST configure `ECS_ENABLE_SPOT_INSTANCE_DRAINING=true` in the launch template user data so ECS drains tasks on Spot interruption (2-minute warning).
- The system MUST configure multiple instance types in the launch template as fallbacks for Spot capacity (e.g., `g4dn.xlarge`, `g4dn.2xlarge`) per Section 8, row 3.
- The system MUST survive a Spot interruption mid-processing: the circuit breaker detects the connection drop, `_wait_for_healthy` polls until a replacement instance is ready, and the job retries successfully.
- The system MUST NOT lose a job permanently due to Spot interruption (retry logic must recover).

## 4. Out of Scope

- Application code changes (all completed in PRD-1; retry/circuit breaker already handles connection drops)
- Multi-region Spot fleet (not needed at current scale)
- Automatic fallback to On-Demand (can be added later if Spot availability is consistently poor)

## 5. Code Changes

No application code changes required. The existing retry logic and circuit breaker handle Spot interruptions identically to any other connection drop (the `_wait_for_healthy` path triggered by `RemoteProtocolError` or `ConnectError`, implemented in PRD-1).

### Modified Files

| File | Changes |
|------|---------|
| `terraform/docling.tf` | See Infrastructure Changes below |

## 6. Infrastructure Changes

All changes in `terraform/docling.tf`:

| Resource | Change |
|----------|--------|
| `aws_launch_template.docling` | Add `instance_market_options { market_type = "spot" }` OR configure Spot via ASG mixed instances policy. Add `ECS_ENABLE_SPOT_INSTANCE_DRAINING=true` to user data script. |
| `aws_autoscaling_group.docling` | Configure `mixed_instances_policy` with multiple instance types: `g4dn.xlarge` (primary), `g4dn.2xlarge` (fallback). Set `on_demand_base_capacity = 0`, `spot_allocation_strategy = "capacity-optimized"`. |

## 7. Verification

1. **Deploy Spot configuration:**
   - `terraform apply` — confirm ASG launches Spot instances (check instance lifecycle in EC2 console).
   - Confirm Spot price is ~$0.16-0.19/hr (check Spot pricing history for region/AZ).

2. **Normal operation test:**
   ```
   uv run scripts/batch_run.py
   ```
   - All 31 documents MUST process successfully on Spot instances.
   - Processing times should match PRD-2 GPU baseline (~15-25s/doc).

3. **Spot interruption simulation:**
   - Submit a document, then manually terminate the EC2 instance mid-processing via AWS console.
   - Observe:
     - ECS drains the task (2-minute Spot warning).
     - Circuit breaker detects connection drop.
     - ASG launches a replacement Spot instance.
     - `_wait_for_healthy` polls until new instance is ready.
     - Job retries and completes successfully.
   - The job MUST complete (possibly after delay), NOT fail permanently.

4. **Scale-to-zero still works:**
   - After all jobs complete, confirm system scales back to 0 Spot instances.
   - Submit a new document from cold — confirm cold start + Spot launch works end-to-end.

5. **Monitor for 2 weeks:**
   - Track Spot interruption rate (Section 10: "Spot interruption rate" alarm).
   - Track job failure rate — should not increase vs On-Demand baseline.
   - Track cost — expected ~$7-9/mo for GPU compute (Section 5, Option A).
   - If interruption rate > 1/day or causes user-visible failures, consider rollback.

## 8. Risks

| Risk | Source | Mitigation |
|------|--------|------------|
| Spot interruption mid-processing | Section 8, row 2 | `ECS_ENABLE_SPOT_INSTANCE_DRAINING=true` gives 2-min warning. Retry logic + circuit breaker handle connection drops. `_wait_for_healthy` polls until replacement is ready. |
| g4dn.xlarge Spot capacity unavailable | Section 8, row 3 | Multiple instance types in ASG (`g4dn.xlarge`, `g4dn.2xlarge`). `capacity-optimized` allocation strategy. If all Spot unavailable, manual fallback to On-Demand (rollback plan). |
| Frequent interruptions degrade user experience | N/A | Monitor interruption rate for 2 weeks. If > 1/day, consider `on_demand_base_capacity = 1` to keep one On-Demand instance as baseline. |
| In-flight job lost on Spot interruption | Section 12, item 1 | Pre-existing issue: `BackgroundTask` has no at-least-once delivery. Spot makes this more visible. The 2-minute drain warning + retry should handle most cases, but a Redis-backed processing queue (tracked separately) would be more robust. |
| Docling result lost on Spot interruption | Section 12, item 3 | Pre-existing issue: no result checkpointing. If interruption occurs after docling completes but before S3 upload, GPU work is lost. Tracked separately. |

## 9. Rollback Plan

1. Remove `instance_market_options` / revert `mixed_instances_policy` to On-Demand in `terraform/docling.tf`.
2. Remove `ECS_ENABLE_SPOT_INSTANCE_DRAINING=true` from user data (optional, harmless to leave).
3. `terraform apply` — ASG will launch On-Demand instances on next scale-out.
4. System returns to PRD-3 state (scale-to-zero On-Demand GPU).
5. Cost returns to ~$24/mo for GPU compute.

This is a safe rollback. No code changes, no data loss. Existing Spot instances will be replaced by On-Demand on next scaling cycle.
