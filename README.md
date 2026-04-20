# Vaner

[![CI](https://github.com/Borgels/vaner/actions/workflows/ci.yml/badge.svg)](https://github.com/Borgels/vaner/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Borgels/vaner/actions/workflows/codeql.yml/badge.svg)](https://github.com/Borgels/vaner/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/Borgels/vaner)](https://github.com/Borgels/vaner/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Borgels/vaner/total?label=Downloads)](https://github.com/Borgels/vaner/releases)
[![License](https://img.shields.io/github/license/Borgels/vaner)](LICENSE)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Borgels/vaner/badge)](https://scorecard.dev/viewer/?uri=github.com/Borgels/vaner)

> Status: alpha (pre-1.0). Interfaces may evolve quickly while we stabilize core behavior.

Vaner is a local-first predictive context engine for AI coding workflows. It
uses idle time to anticipate likely next prompts, pre-build useful context, and
serve the best context package quickly when the real prompt arrives.

## Quickstart

1. Install:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash
```

2. Configure your clients (interactive wizard):

```bash
vaner init --path .
```

3. Start Vaner:

```bash
vaner up --path .
```

Asciinema demo: coming soon.

## Documentation

Most documentation lives at [docs.vaner.ai](https://docs.vaner.ai):

- Getting started: [docs.vaner.ai/getting-started](https://docs.vaner.ai/getting-started)
- Integrations: [docs.vaner.ai/integrations](https://docs.vaner.ai/integrations)
- Configuration: [docs.vaner.ai/configuration](https://docs.vaner.ai/configuration)
- Architecture: [docs.vaner.ai/architecture](https://docs.vaner.ai/architecture)
- Security: [docs.vaner.ai/security](https://docs.vaner.ai/security)
- CLI reference: [docs.vaner.ai/cli](https://docs.vaner.ai/cli)
- Examples: [docs.vaner.ai/examples](https://docs.vaner.ai/examples)

## Community

- Contributing guide: `CONTRIBUTING.md`
- Security policy: `SECURITY.md`
- Governance model: `GOVERNANCE.md`
- Maintainer roles and sensitive-resource ownership: `MAINTAINERS.md`
- Code of conduct: `CODE_OF_CONDUCT.md`
- Support channels: `SUPPORT.md`
- Examples: `examples/`

## OpenSSF Best Practices

Vaner is preparing an OpenSSF Best Practices submission.

## License

Apache-2.0. Copyright 2026 Borgels Olsen Holding ApS (VAT DK39700425).
