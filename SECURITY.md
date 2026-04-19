# Security Policy

## Supported Versions

Security fixes are applied to the latest stable release line.
Pre-release or experimental builds may receive best-effort fixes but are not
guaranteed a patch window.

Current support intent:

- Latest stable release line: receives security fixes.
- Older release lines: may be closed to security fixes after a newer stable line
  is published.
- End-of-support for an older line is communicated through release notes and/or
  GitHub Security Advisories.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately. Do not open public GitHub issues for security disclosures.

Primary contact email: [security@vaner.ai](mailto:security@vaner.ai)

Security guidance URL: [https://docs.vaner.ai/security](https://docs.vaner.ai/security)

For encrypted disclosure channels and additional process details, also see [docs.vaner.ai/security](https://docs.vaner.ai/security).

## Response Targets

- Initial acknowledgment within 2 days
- Triage and severity assessment within 7 days
- Coordinated disclosure target within 90 days from initial report, unless a different disclosure window is agreed with the reporter

## Public Vulnerability Publication

Vaner uses GitHub Security Advisories as the canonical public disclosure channel
for confirmed vulnerabilities.

Advisories should include, when available:

- affected version ranges
- fixed version(s)
- mitigation or workaround guidance
- severity and impact notes

## Code Review & Branch Protection

The `main` branch is protected with:

- linear history enforcement
- admin enforcement
- strict required status checks
- required pull-request review gates
- required conversation resolution
- disabled force-pushes and branch deletion

Configured required status checks currently include:

- `verify (ubuntu-latest, 3.12)`
- `examples-smoke`
- `no-moat-paths`
- `actionlint`

Configured review requirements currently include:

- code owner review required
- last-push approval required
- two approving reviews required

Vaner is currently maintained by a single maintainer. Self-merges run through CI, CodeQL, actionlint, and zizmor checks and may use GitHub admin override when review requirements cannot be satisfied by a second maintainer.

The OpenSSF Scorecard `Code-Review` signal can remain low in this solo-maintainer period until additional reviewers are onboarded.

## GitHub Security Features (Current)

The repository currently has these GitHub security features enabled:

- Security policy
- Security advisories
- Dependabot alerts and security updates
- Code scanning alerts
- Secret scanning alerts
- Secret scanning push protection

Current notable settings:

- Private vulnerability reporting in GitHub is disabled; private disclosure is
  handled via `security@vaner.ai`.
- GitHub Actions default workflow token permissions are set to read-only.

## Disclosure Process

After a vulnerability report is received, Vaner maintainers validate impact, assign severity, and coordinate remediation before public disclosure.
Coordinated vulnerability disclosure is used for all confirmed issues to reduce risk to users.

## Secrets and Credentials Policy

Vaner must not store plaintext production secrets in version control.
This includes API keys, long-lived tokens, signing keys, private certificates,
passwords, and credential dumps.

Required controls:

- Keep secrets in GitHub Secrets, environment variables, or equivalent secret
  stores; never hardcode them in committed source.
- Restrict secret access to maintainers who need operational access.
- Rotate credentials after suspected exposure, maintainer offboarding, or
  provider-initiated compromise events.
- Revoke and replace leaked credentials immediately, then document remediation in
  the incident/advisory record as appropriate.

Repository scanning:

- GitHub Secret Scanning alerts are enabled.
- Contributors are expected to review diffs and avoid committing sensitive files.

## SCA (Dependency Vulnerability and License) Policy

Vaner uses software composition analysis (SCA) checks, including `pip-audit`, to
surface dependency risk.

Remediation thresholds:

- Critical or High vulnerabilities in runtime dependencies must be remediated or
  formally risk-accepted before release.
- Medium vulnerabilities must be triaged before release with either remediation
  or explicit risk acceptance and timeline.
- License issues that conflict with project licensing/distribution requirements
  must be resolved before release.

Suppression policy:

- Temporary suppressions require maintainer approval, a written rationale, and
  an expiration/revisit date.
- Suppressions must be removed when an upstream fix becomes available and is
  compatible.

## SAST Policy

Vaner uses static analysis tooling (including CodeQL) to detect code-level
security weaknesses.

Remediation thresholds:

- Critical or High findings in changed code must be remediated or formally
  risk-accepted before merge.
- Findings affecting release artifacts must be remediated or risk-accepted before
  release.

Suppression policy:

- Suppression requires maintainer approval with documented justification.
- Suppressions should be narrowly scoped and periodically reviewed.

## Security Documentation

Public-facing security and privacy guidance is published at
[docs.vaner.ai/security](https://docs.vaner.ai/security).

Additional repository security artifacts:

- `docs/threat-model.md`
- `docs/security-assessment.md`

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
