# PRD-2: GPU Acceleration

**Parent proposal:** `docs/plans/docling-gpu-scale-to-zero.md` (Phase 2, Section 7)
**Status:** Draft
**Dependencies:** PRD-1 complete (docling decoupled, running on CPU ARM, all code changes applied)

---

## 1. Goal

Swap the CPU ARM docling image and instance type for a CUDA GPU image on g4dn.xlarge, achieving ~6x processing speed improvement (90s/doc -> 15-25s/doc).

## 2. Background

PRD-1 established the decoupled architecture with docling running on c6g.xlarge ARM Spot behind an internal ALB. All application code changes (circuit breaker, pre-warm, scaling metric) are already deployed. This phase changes only the compute layer — swapping the instance type, AMI, and container image — to enable GPU-accelerated inference. This corresponds to Option B from Section 5 (On-Demand pricing; Spot is deferred to PRD-4).

Performance expectations from Section 4: layout analysis 14x faster, OCR 8x faster, table structure 4.3x faster on T4 GPU.

## 3. Requirements

- The system MUST have `docling-serve-cu128:1.12.0` pushed to ECR (pinned version, not latest — Section 8, row 9).
- The system MUST use `g4dn.xlarge` instances (4 vCPU, 16 GB, 1x T4 GPU).
- The system MUST use an ECS GPU-optimized AMI (not the ARM AMI from PRD-1), pinned via `data.aws_ami` filter.
- The system MUST add `gpu` resource requirement to the docling task definition (`resourceRequirements = [{ type = "GPU", value = "1" }]`).
- The system MUST use On-Demand pricing for g4dn.xlarge in this phase (Spot deferred to PRD-4).
- The system MUST keep `min_capacity=1` (always-on, scale-to-zero deferred to PRD-3).
- The system MUST process documents at ~15-25s average (down from ~90s on CPU), validated via batch_run.py.

## 4. Out of Scope

- Scale-to-zero (`min_capacity=0`) (PRD-3)
- Spot pricing for GPU instances (PRD-4)
- Application code changes (all completed in PRD-1)
- ALB or security group changes (unchanged from PRD-1)

## 5. Code Changes

No application code changes required. All code changes were completed in PRD-1.

### Modified Files

| File | Changes |
|------|---------|
| `terraform/docling.tf` | See Infrastructure Changes below |

## 6. Infrastructure Changes

All changes in `terraform/docling.tf`:

| Resource | Change |
|----------|--------|
| `data.aws_ami.ecs_gpu_optimized` | Change AMI filter from ARM ECS-optimized to x86 ECS GPU-optimized AMI. Pin to specific version. |
| `aws_launch_template.docling` | Change `instance_type` from `c6g.xlarge` to `g4dn.xlarge`. Update AMI reference. Update architecture from `arm64` to `x86_64`. Ensure 30 GB gp3 root volume (g4dn has local NVMe but root volume is still needed). Remove Spot config if present (On-Demand for this phase). |
| `aws_ecs_task_definition.docling` | Change container image from `docling-serve-cpu` to `docling-serve-cu128:1.12.0` (ECR URI). Add `resourceRequirements = [{ type = "GPU", value = "1" }]`. Adjust memory/CPU reservations for g4dn.xlarge (up to 15360 MB memory, 4096 CPU units). |
| `aws_autoscaling_group.docling` | Remove ARM-specific instance type overrides if any. Ensure `g4dn.xlarge` is the primary instance type. |

## 7. Verification

1. **Push CUDA image to ECR:**
   ```bash
   # Pull, tag, and push the CUDA image
   docker pull quay.io/docling-project/docling-serve-cu128:1.12.0
   docker tag quay.io/docling-project/docling-serve-cu128:1.12.0 <account>.dkr.ecr.<region>.amazonaws.com/docling-serve:cu128-1.12.0
   docker push <account>.dkr.ecr.<region>.amazonaws.com/docling-serve:cu128-1.12.0
   ```

2. **Deploy and verify GPU task launches:**
   - `terraform apply` — confirm new g4dn.xlarge instance launches.
   - Confirm ECS task starts with GPU resource allocated.
   - Confirm ALB health check passes.

3. **Run batch test:**
   ```
   uv run scripts/batch_run.py
   ```
   - All 31 test documents MUST process successfully.
   - Average processing time MUST be ~15-25s/doc (down from ~90s baseline).
   - Compare results side-by-side with PRD-1 CPU baseline.

4. **Verify GPU utilization:**
   - Check CloudWatch GPU utilization metric for the g4dn instance.
   - Confirm GPU memory usage is reasonable (T4 has 16 GB VRAM).
   - If GPU utilization is 0%, the CUDA image is falling back to CPU — investigate.

5. **Verify cost:**
   - Monitor daily cost for g4dn.xlarge On-Demand (~$0.526/hr x hours running).
   - With `min_capacity=1`, expected cost is ~$0.526/hr x 24hr x 30d = ~$379/mo for the instance alone. This exceeds budget. Proceed to PRD-3 (scale-to-zero) promptly.

## 8. Risks

| Risk | Source | Mitigation |
|------|--------|------------|
| Docling CUDA image compatibility issues | Section 8, row 9 | Image pinned to tested version `cu128:1.12.0`. Pushed to ECR (no runtime pull from quay.io). Test in staging before production. |
| Mixed AMI versions in ASG | Section 8, row 10 | AMI pinned via data source filter. Terraform plan will show AMI change. |
| g4dn.xlarge On-Demand cost with min=1 | N/A (budget risk) | At $0.526/hr always-on, monthly cost is ~$379 for compute alone — **over budget**. PRD-3 (scale-to-zero) MUST follow immediately. Do not leave min=1 on GPU for more than 1-2 days of validation. |
| GPU not utilized (CUDA fallback to CPU) | Section 8, row 9 | Verify GPU utilization metrics immediately after deployment. If zero, check CUDA driver version, GPU resource allocation in task definition. |

## 9. Rollback Plan

1. Revert `terraform/docling.tf` to PRD-1 state (c6g.xlarge ARM, CPU image).
2. `terraform apply` — ASG will launch ARM instances, terminate GPU instances.
3. Verify docling service recovers with CPU image.

Alternatively, if the GPU image itself is the problem but g4dn works:
1. Update task definition to use CPU image on g4dn (wasteful but functional).
2. Investigate CUDA compatibility issue.
3. Re-deploy with fixed CUDA image.
