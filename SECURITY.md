# Security Policy

## Supported Versions

Security fixes are applied to the latest release line.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately. Do not open public GitHub issues for security disclosures.

Primary contact email: [security@vaner.ai](mailto:security@vaner.ai)

Security guidance URL: [https://docs.vaner.ai/security](https://docs.vaner.ai/security)

For encrypted disclosure channels and additional process details, also see [docs.vaner.ai/security](https://docs.vaner.ai/security).

## Response Targets

- Initial acknowledgment within 2 days
- Triage and severity assessment within 7 days
- Coordinated disclosure target within 90 days from initial report, unless a different disclosure window is agreed with the reporter

## Code Review & Branch Protection

The `main` branch is protected with linear history, admin enforcement, strict status checks, and pull-request review gates.
Required status checks include `verify (ubuntu-latest, 3.12)`, `examples-smoke`, `no-moat-paths`, and `actionlint`.

Vaner is currently maintained by a single maintainer. Self-merges run through CI, CodeQL, actionlint, and zizmor checks and may use GitHub admin override when review requirements cannot be satisfied by a second maintainer.

The OpenSSF Scorecard `Code-Review` signal can remain low in this solo-maintainer period until additional reviewers are onboarded.

## Disclosure Process

After a vulnerability report is received, Vaner maintainers validate impact, assign severity, and coordinate remediation before public disclosure.
Coordinated vulnerability disclosure is used for all confirmed issues to reduce risk to users.

## Security Documentation

Public-facing security and privacy guidance is published at
[docs.vaner.ai/security](https://docs.vaner.ai/security).

## Verify Release Integrity

Verify container image signatures (keyless OIDC via GitHub Actions):

```bash
cosign verify ghcr.io/borgels/vaner:latest \
  --certificate-identity-regexp "https://github.com/Borgels/vaner/.github/workflows/docker-release.yml@refs/tags/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

Verify Python release provenance (SLSA):

```bash
slsa-verifier verify-artifact \
  --provenance-path multiple.intoto.jsonl \
  --source-uri github.com/Borgels/vaner \
  dist/vaner-*.whl
```

## OpenSSF Best Practices

Vaner is preparing an OpenSSF Best Practices submission.