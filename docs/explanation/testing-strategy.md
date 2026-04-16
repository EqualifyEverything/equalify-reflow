# Testing strategy

The test suite is split into three tiers defined by **what they touch**, not **what they test**. Each tier answers a different question and runs at a different phase of the development loop.

| Tier | Marker | Touches | Time | Answers |
|---|---|---|---|---|
| Unit | `@pytest.mark.unit` | Nothing external — all I/O mocked | ~30s parallel | Does this function behave correctly in isolation? |
| Integration | `@pytest.mark.integration` | Real Redis + Floci (testcontainers); AI responses mocked | ~2min | Do services compose correctly across real infrastructure? |
| End-to-end | `@pytest.mark.slow` | Full stack, real Bedrock calls, real PDF fixtures | ~5min | Does the whole pipeline still produce expected output? |

## Why three tiers instead of one

A single "just run all the tests" philosophy breaks down for a system like this:

- **Unit speed is load-bearing.** You run these after every keystroke in CI and between commits. They have to be fast (parallelized, in-process) and deterministic (no network). That forces mocking.
- **Integration catches what unit can't.** Serialization bugs, Redis key-scheme drift, Lua-script atomicity, S3 signing — none of these show up when the client is a mock. Testcontainers gives us a real Redis and a real S3 emulator without polluting the local environment.
- **End-to-end catches what even integration can't.** Real LLM calls drift. Prompts that work on fixtures today can fail on real documents tomorrow. E2E runs against a curated corpus of small PDFs to catch regressions in output quality.

You don't want every test to be E2E (slow, expensive, flaky from LLM nondeterminism). You don't want every test to be unit (misses the hard bugs). The three-tier split gives each tier the narrowest remit where it's actually useful.

## When each tier runs

- **Unit** — every push to every branch, via the `test-fast.yml` workflow. Developers also run locally before commits (`make test-fast`).
- **Integration** — on every PR to `main` or `develop`, via `test-integration.yml`. Developers run before opening PRs (`make test-integration`).
- **E2E** — on merges to `main`, via `test-e2e.yml`. Developers run before merging anything with pipeline or prompt changes (`make test-e2e`).

## Fail-open for flaky LLM calls

E2E tests that exercise real Bedrock inherit some LLM nondeterminism. Rather than pinning outputs, these tests assert structural properties (heading hierarchy valid, no broken tables, PII flags fired when expected). Individual output string drift does not fail the build; structural regressions do.

For running tests, see [how to run tests](../how-to/run-tests.md). For CI workflow details, see [CI workflows reference](../reference/ci-workflows.md).
