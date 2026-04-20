# Vaner Security Assessment Summary

This document summarizes recurring security assessment activities and outcomes
for the project. It complements `SECURITY.md` and the threat model in
`docs/threat-model.md`.

## Assessment Cadence

Security assessment is performed:

- continuously through CI checks on pull requests and main branch updates
- before release publication for tagged releases
- after significant architecture or trust-boundary changes

## Assessment Inputs

- static analysis findings (CodeQL and lint/security checks)
- dependency vulnerability analysis (`pip-audit`)
- container scanning and release pipeline security checks
- threat model updates and design review notes
- disclosure and incident learnings from security reports/advisories

## Current Security Controls (High Level)

- coordinated vulnerability disclosure process (`SECURITY.md`)
- GitHub Security Advisories for public vulnerability publication
- dependency and static-analysis checks in CI
- signed/provenance-backed release pipeline artifacts
- documented governance for sensitive-resource access

## Findings Handling Policy

Finding disposition categories:

- remediate immediately
- remediate before release
- temporarily accept risk with documented rationale and review date

High-severity findings in changed code or release-critical paths are expected to
be remediated or explicitly risk-accepted before merge/release per `SECURITY.md`.

## Baseline 3 Coverage Notes

This assessment process supports:

- periodic understanding of likely and impactful security problems
- traceable security decision making for remediation and risk acceptance

Additional maturity targets:

- expand threat modeling depth for new interfaces and deployment modes
- tighten enforcement evidence for merge/release blocking thresholds
- evaluate lightweight VEX publication when vulnerability volume warrants it

## Review and Ownership

- Maintainer owner: `@abolsen`
- Review trigger: major release changes, significant security findings, or
  changes to external interfaces and trust boundaries
