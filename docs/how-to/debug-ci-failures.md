# How to debug a CI failure

When a workflow fails in GitHub Actions, work through this list before pinging someone.

## 1. Read the workflow log

Actions tab → failed run → expand the red step. The traceback is usually enough. Ignore warnings; focus on the first error.

## 2. Reproduce locally with the matching tier

| CI workflow | Local command |
|---|---|
| `test-fast.yml` | `make test-fast` |
| `test-integration.yml` | `make test-integration` |
| `test-e2e.yml` | `make test-e2e` |

The local commands run the same pytest invocations as CI. If your local run passes, the problem is environmental.

## 3. If it only fails in CI

Usually one of:

- **Ordering issue** — tests pass individually but fail in parallel. Run `make test-integration` locally without `-n auto`: `docker compose exec api-gateway uv run pytest -m integration -v`. If this reproduces, the test has hidden state (class-level fixture, Redis keys not scoped, shared temp file).
- **Stale testcontainers cache** — `docker system prune -f` and retry.
- **Bedrock credential drift** — E2E only. The CI job uses repo secrets; your local run uses SSO. Check the IAM scope.
- **Timing/flake** — rare but real. Rerun the failed job. If it reproduces twice, treat as real.

## 4. Verbose locally

```bash
# Full trace, stdout inline
docker compose exec api-gateway uv run pytest tests/path/to/test.py -vvs

# Only the failing test
docker compose exec api-gateway uv run pytest tests/path/to/test.py::test_name -vvs

# With Python debugger on failure
docker compose exec api-gateway uv run pytest tests/path/to/test.py::test_name --pdb
```

## 5. Service logs

If integration/E2E fails and the error looks like "Connection refused" or "Bucket not found":

```bash
make logs-api        # API logs
docker compose logs redis
docker compose logs floci
make health          # Does the stack say it's healthy?
```

## 6. Download the CI coverage artifact

If the failure is coverage-related (line count dropped, uncovered branch surfaced), the artifact has the HTML report with line-by-line detail. Actions tab → run → Artifacts section.
