# Vaner

[![CI](https://github.com/Borgels/Vaner/actions/workflows/ci.yml/badge.svg)](https://github.com/Borgels/Vaner/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Borgels/Vaner/actions/workflows/codeql.yml/badge.svg)](https://github.com/Borgels/Vaner/actions/workflows/codeql.yml)
[![GitHub Release](https://img.shields.io/github/v/release/Borgels/Vaner?label=GitHub%20Release)](https://github.com/Borgels/Vaner/releases)
[![Downloads](https://img.shields.io/github/downloads/Borgels/Vaner/total?label=Downloads)](https://github.com/Borgels/Vaner/releases)
[![PyPI](https://img.shields.io/pypi/v/vaner)](https://pypi.org/project/vaner/)
[![Python](https://img.shields.io/pypi/pyversions/vaner)](https://pypi.org/project/vaner/)
[![License](https://img.shields.io/github/license/Borgels/Vaner)](LICENSE)
[![Codecov](https://codecov.io/gh/Borgels/Vaner/branch/main/graph/badge.svg)](https://codecov.io/gh/Borgels/Vaner)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Borgels/vaner/badge)](https://scorecard.dev/viewer/?uri=github.com/Borgels/vaner)

> Status: alpha (pre-1.0). Interfaces may evolve quickly while we stabilize core behavior.

Vaner is a local-first predictive context engine for AI coding workflows. It
uses idle time to anticipate likely next prompts, pre-build useful context, and
serve the best context package quickly when the real prompt arrives.

## Demo

CLI walkthrough:

```bash
vaner init --path .
vaner daemon start --no-once --path .
vaner query "where is auth enforced?" --explain --path .
vaner inspect --last --path .
vaner why --list --path .
```

Asciinema demo: coming soon.

## Install

```bash
curl -fsSL https://vaner.ai/install.sh | bash
```

Installer source for review: [`scripts/install.sh`](scripts/install.sh).

From source:

```bash
pip install .
```

## Quickstart

```bash
vaner init --path .
vaner daemon start --no-once --path .
vaner query "where is auth enforced?" --explain --path .
vaner inspect --last --path .
vaner why --list --path .
```

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
- Code of conduct: `CODE_OF_CONDUCT.md`
- Support channels: `SUPPORT.md`
- Examples: `examples/`

## License

Apache-2.0. Copyright 2026 Borgels Olsen Holding ApS (VAT DK39700425).
