# Security Policy

## Supported Versions

Equalify Reflow is currently in beta. The `main` branch is the only supported version. Tagged releases (e.g. `v0.1.0-beta.1`) are snapshots for reference; fixes land on `main`.

## Reporting a Vulnerability

If you believe you've found a security vulnerability in Equalify Reflow, please **do not** open a public GitHub issue. Instead, report it privately via one of:

- **GitHub Security Advisory:** https://github.com/EqualifyEverything/equalify-reflow/security/advisories/new
- **Email the maintainers:** `security@equalify.app`  *(TODO: operator to confirm or replace before repo goes public)*

We aim to acknowledge reports within 3 business days and to provide a remediation timeline within 7 business days for confirmed issues.

## Scope

**In scope:**

- The Equalify Reflow API server (`src/`)
- The Pipeline Viewer client (`clients/viewer/`)
- Project dependencies as declared in `pyproject.toml`

**Out of scope:**

- Third-party services the project integrates with (AWS, Anthropic, Microsoft Presidio, IBM Docling). Report those directly to the vendor.
- Self-hosted deployments configured differently from the project defaults. We can advise but cannot patch environments we do not control.

## What to include in a report

- A description of the issue and its impact
- Steps to reproduce, ideally a minimal proof of concept
- The affected version or commit SHA
- Your preferred public credit (or a request to remain anonymous)

Thank you for helping keep Equalify Reflow and its users safe.
