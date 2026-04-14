## Summary

<!-- One or two sentences on what this PR changes and why. The "why" matters more than the "what" — the diff shows the what. -->

## Related issue

<!-- Link an issue if one exists. Otherwise, explain why this change needs no issue. -->

Closes #

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor (no behaviour change)
- [ ] Documentation
- [ ] Prompt / agent improvement (touches `src/agents/` or the AI prompts in `src/services/pipeline_viewer.py`)
- [ ] Infrastructure / CI
- [ ] Other: <!-- explain -->

## Testing

<!-- What did you run? Which tests did you add? -->

- [ ] `make test-fast` passes
- [ ] `make test-integration` passes (if touching services or workers)
- [ ] Manually verified in `make dev` (if touching API endpoints or the Pipeline Viewer)

## Checklist

- [ ] I've read [CONTRIBUTING.md](../CONTRIBUTING.md) and followed its conventions
- [ ] I've updated documentation where relevant (`README.md`, `docs/`, `AGENTS.md`)
- [ ] My commits follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, etc.)
- [ ] I've added or updated tests for any behaviour change
- [ ] My change does not regress accessibility of generated markdown (if touching pipeline steps)
