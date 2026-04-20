# Maintainers

Current maintainers:

- `@abolsen` — project direction, architecture, releases, and security triage

Scope:

- Review pull requests and maintain issue hygiene
- Keep release workflows healthy
- Maintain community and contribution standards

## Sensitive Resource Roles

Current sensitive-resource operator assignments:

- Repository administration and branch/ruleset management: `@abolsen`
- Release management (GitHub Releases, package/container publishing): `@abolsen`
- CI/CD and repository secret administration: `@abolsen`
- Security triage and advisory publication: `@abolsen`

## Access Model

Vaner currently operates as a single-maintainer project.
This creates unavoidable cases where self-approval or admin override may be
required to keep releases and security fixes moving.

Compensating controls include:

- required CI and security automation checks on pull requests and main branch
- public change history and release artifacts
- documented security disclosure and advisory process in `SECURITY.md`

As additional maintainers are onboarded, Vaner will move toward dual-control for
sensitive operations (review and approval by a second maintainer).
