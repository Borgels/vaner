# Vaner Threat Model (Baseline)

This document captures a lightweight threat model for Vaner's current
architecture and release process. It is intended to be updated as major
features or trust boundaries change.

## Scope

In scope:

- local daemon and CLI operation
- context artifact generation and storage
- OpenAI-compatible proxy and MCP integration modes
- release build/sign/publish workflows

Out of scope:

- third-party model provider internals
- user-managed reverse proxy hardening details

## Assets and Security Objectives

Primary assets:

- repository and local workspace source code
- prepared context artifacts and metadata
- API credentials and runtime secrets
- release artifacts and provenance metadata

Security objectives:

- preserve confidentiality of sensitive local/project data
- preserve integrity of generated context and release artifacts
- maintain availability of core local functionality
- provide auditable release provenance

## Actors

Trusted actors:

- maintainers operating project infrastructure and releases
- contributors submitting reviewed pull requests
- local users running Vaner in controlled environments

Potential adversaries:

- external attackers attempting credential theft or supply-chain compromise
- malicious or compromised dependencies
- insiders or compromised maintainer accounts
- network adversaries attempting MITM on distribution channels

## Entry Points and Attack Surfaces

- CLI command input and config files
- local daemon HTTP endpoints (proxy/context APIs)
- MCP integration boundaries
- CI/CD workflows and release pipelines
- dependency update flows

## Key Threats and Mitigations

### Sensitive data leakage

Threat:

- secrets or sensitive source content committed or exposed in logs/artifacts

Mitigations:

- default exclusion patterns for known secret-like files
- documented secret handling policy in `SECURITY.md`
- GitHub secret scanning alerts
- contributor guidance against committing sensitive data

### Supply-chain dependency compromise

Threat:

- vulnerable or malicious dependency introduced through updates

Mitigations:

- pinned, hash-locked requirements for CI/release/runtime
- SCA checks (`pip-audit`) in CI
- documented SCA remediation thresholds in `SECURITY.md`

### Tampered release artifacts

Threat:

- consumers receive modified or impersonated release artifacts

Mitigations:

- signed/provenance-backed release workflows
- cosign keyless signatures for container artifacts
- SLSA provenance generation and verification guidance in `SECURITY.md`

### Unauthorized privileged changes

Threat:

- compromised collaborator or misconfigured permissions allow unsafe merges/releases

Mitigations:

- branch protection and required checks on primary branch
- least-privilege governance policy for sensitive resources
- documented elevated permission grant/review process in `GOVERNANCE.md`

## Residual Risks and Follow-ups

- single-maintainer operations can require admin override in rare cases; this is
  documented with compensating controls in `MAINTAINERS.md`
- threat model depth should expand as deployment patterns and integrations grow
- this document should be reviewed for each new release line with material
  architecture or trust-boundary changes
