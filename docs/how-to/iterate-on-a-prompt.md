# How to iterate on a prompt

Agents live as module-level string constants in `src/agents/*.py`. Because the stack has hot reload, prompt changes take effect inside the running container as soon as you save the file.

## 1. Find the prompt

Each agent's system prompt is a module-level constant. Grep by the constant name or by the `Agent(` call site:

```bash
grep -rn "Agent(" src/services/pipeline_viewer.py
```

The call sites reference prompt modules in `src/agents/`. Open the one you care about; the prompt is usually named `*_SYSTEM_PROMPT` or similar.

## 2. Reproduce the failing case

```bash
make dev                                   # Boot the stack
open http://localhost:8080/                # Open the viewer
# Upload a small PDF that exhibits the failure
```

Step through the versioned output per phase to isolate which step misbehaves. The viewer shows the markdown diff between versions and the change ledger for each step, so you can see the exact edit that went wrong.

## 3. Edit the prompt

Edit the constant in `src/agents/`. Save. The api-gateway container picks up the change automatically (hot reload via the `./src` bind mount).

## 4. Re-run the pipeline on the same document

Because each phase is versioned in S3, you don't have to reprocess from v0. Resubmit the same document via the viewer; previous versions are preserved, so you can inspect the diff for just the phase you changed.

## 5. Run the tests

```bash
make test-fast          # Quick signal, catches unit regressions
make test-integration   # Parity with real Redis + Floci
make test-e2e           # Real Bedrock calls against fixture PDFs
```

If a prompt change breaks tests, the fix is usually a coordinated update to both the prompt and the fixtures — the model's output format changed and snapshot-style assertions need to catch up. See [how to run tests](run-tests.md) for test commands.

## 6. Include the before/after markdown diff in the PR

Reviewers should see the user-visible impact, not just the prompt string change. Paste the relevant portion of the markdown diff (from the viewer) into the PR body.

## Tracing tips

Enable Logfire for full agent traces when hunting a regression:

```bash
LOGFIRE_ENABLED=true make dev
```

This logs every tool call, subagent invocation, and token count, which is invaluable when trying to understand why the main agent took a particular path.

The raw agent output for any completed job is also at `GET /api/v1/documents/{job_id}/ledger` — useful for auditing without re-running.
