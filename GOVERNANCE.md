# Governance

This repository follows a stewarded open-source model.

## Decision model

- Day-to-day technical decisions are made by maintainers.
- Larger roadmap and breaking-change decisions are proposed via issues/PRs and discussed publicly.
- Consensus is preferred; when consensus is not possible, maintainers decide and document rationale.

## Maintainer expectations

- Review and triage issues/PRs consistently.
- Keep CI, release, and security hygiene in good standing.
- Enforce contribution and code-of-conduct policies.

## Sensitive Resource Access

Sensitive resources include:

- repository admin and branch/ruleset configuration
- release publishing channels (GitHub Releases, package registries, container registries)
- CI/CD secrets and deployment credentials
- security disclosure handling channels

Access to sensitive resources follows least-privilege principles and should be
limited to maintainers with a clear operational need.

## GitHub Enforcement Snapshot

Current GitHub configuration relevant to access control and change control:

- Organization default repository permission is `read`.
- Branch protection on `main` enforces required checks and review gates.
- GitHub Actions default workflow token permission is `read`.

Current limitation:

- Organization-wide mandatory 2FA is not yet enabled.
  Until this is enabled, maintainer accounts are expected to use strong MFA
  individually and to keep session/device hygiene strict.

## Elevated Permission Grant Policy

Before granting elevated permissions to a collaborator, maintainers should:

1. confirm a sustained record of constructive contributions
2. confirm identity and communication channels sufficient for coordinated response
3. document the requested access scope and rationale
4. grant the minimum required permission set
5. record who approved the grant and when

Where feasible, at least one existing maintainer other than the requestor should
review and approve the grant.

## Elevated Permission Review and Revocation

- Access grants should be reviewed periodically and reduced when no longer needed.
- Elevated access must be revoked promptly after role changes, inactivity, or
  suspected compromise.
- Security-relevant access changes should be traceable through repository/org
  audit records.

## Future evolution

As the maintainer group grows, this file can evolve into a formal voting and RFC process.
