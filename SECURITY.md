# Security Policy

## Supported Versions

Security fixes are applied to the latest release line.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately. Do not open public GitHub issues for security disclosures.

Primary contact email: security@vaner.ai

Security guidance URL: https://docs.vaner.ai/security

For encrypted disclosure channels and additional process details, also see [docs.vaner.ai/security](https://docs.vaner.ai/security).

## Response Targets

- Initial acknowledgment within 2 days
- Triage and severity assessment within 7 days
- Coordinated disclosure target within 90 days from initial report, unless a different disclosure window is agreed with the reporter

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
Project badge URL (to be added after registration): https://www.bestpractices.dev/projects/TODO
