# Claude Code Documentation

This directory contains focused documentation for Claude Code to understand and work with this codebase effectively.

## Documentation Structure

- **[architecture.md](architecture.md)** - System design, data flow, service layer, Redis patterns, AWS Bedrock setup
- **[authentication.md](authentication.md)** - API key authentication, Swagger/docs auth, middleware stack order
- **[testing.md](testing.md)** - 3-tier testing strategy, fixtures, markers, running tests
- **[s3-resilience.md](s3-resilience.md)** - Circuit breakers, retry logic, Prometheus metrics
- **[development.md](development.md)** - Adding features (endpoints/workers/services), debugging, common issues

## How to Use

1. Start with **[CLAUDE.md](../../CLAUDE.md)** in the root directory for quick reference
2. Drill down into specific topics as needed using the links above
3. Each doc is self-contained and focused on one area

## Maintenance

When updating these docs:
- Keep each doc focused on its single topic
- Ensure CLAUDE.md stays slim (<150 lines)
- Update links if files are renamed or moved
- Remove outdated information promptly
