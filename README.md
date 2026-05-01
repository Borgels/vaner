# Vaner

[![CI](https://github.com/Borgels/vaner/actions/workflows/ci.yml/badge.svg)](https://github.com/Borgels/vaner/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Borgels/vaner/actions/workflows/codeql.yml/badge.svg)](https://github.com/Borgels/vaner/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/Borgels/vaner)](https://github.com/Borgels/vaner/releases/latest)
[![License](https://img.shields.io/github/license/Borgels/vaner)](LICENSE)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Borgels/vaner/badge)](https://scorecard.dev/viewer/?uri=github.com/Borgels/vaner)

> Status: alpha (pre-1.0). Interfaces may evolve quickly while we stabilize core behavior.

## What is Vaner?

Vaner is a local-first preparation engine for AI coding agents. It turns idle
compute into evidence-backed Prepared Work — review notes, bug hypotheses, docs
drift, virtual diffs, research briefs, ready prediction-backed drafts — and
serves the best fit when the real question arrives.

Prepared Work is non-mutating by default: virtual diffs and exports require
explicit user action. Vaner does not silently edit your files.

## Install

**Most users:** download the desktop app at **[vaner.ai](https://vaner.ai)**.
The desktop app starts the engine, picks a local model for your hardware, and
wires Vaner into the MCP clients it detects on your machine (Claude Code,
Cursor, Zed, VS Code, Claude Desktop, …).

**Power users / CI:** install the CLI directly.

```bash
# Linux/macOS — one-line installer
curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash

# Or via your package manager of choice
pipx install 'vaner[mcp]'
uv tool install 'vaner[mcp]'
```

Full install matrix (flags, non-interactive CI, package upgrade, source build,
retention/expiry): **[docs.vaner.ai/getting-started](https://docs.vaner.ai/getting-started)**.

## First run

```bash
vaner init --path .   # detect hardware, pick a model, wire detected MCP clients
vaner up --path .     # start the daemon + cockpit
```

The cockpit opens at `http://127.0.0.1:8473/` and shows live pipeline state,
prepared work, predictions, goals, and feedback.

## Connect your AI client

Vaner exposes context to your agent over [MCP](https://modelcontextprotocol.io/).
`vaner init` wires the clients it detects automatically. Manual setup:

| Client                                                      | One command                                                                                |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Claude Code (plugin, recommended)                           | `/plugin marketplace add Borgels/vaner` then `/plugin install vaner@vaner`                 |
| Claude Code (manual MCP)                                    | `claude mcp add --transport stdio --scope user vaner -- vaner mcp --path .`                |
| Codex CLI                                                   | `codex mcp add vaner -- vaner mcp --path .`                                                |
| Cursor, VS Code, Zed, Windsurf, Continue, Claude Desktop, Cline, Roo | see **[docs.vaner.ai/integrations](https://docs.vaner.ai/integrations)**          |

## Documentation

Full docs at **[docs.vaner.ai](https://docs.vaner.ai)**:

- [Getting started](https://docs.vaner.ai/getting-started) — install + first run
- [Integrations](https://docs.vaner.ai/integrations) — every supported MCP client
- [Cockpit](https://docs.vaner.ai/cockpit) — live pipeline view + diagnostics
- [Configuration](https://docs.vaner.ai/configuration) — backends, compute, retention
- [CLI reference](https://docs.vaner.ai/cli) — every command
- [MCP tools](https://docs.vaner.ai/mcp) — `vaner.*` namespace
- [Architecture](https://docs.vaner.ai/architecture) — how the engine works
- [Security](https://docs.vaner.ai/security) — local-first guarantees, threat model

## Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for dev setup, testing, CI, the
Claude Code plugin parity rules, and DCO sign-off. Quick start:

```bash
git clone https://github.com/Borgels/vaner.git
cd vaner
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
pytest tests -m "not slow and not integration"
```

## Community

- Security policy: [`SECURITY.md`](SECURITY.md)
- Governance: [`GOVERNANCE.md`](GOVERNANCE.md) · maintainers: [`MAINTAINERS.md`](MAINTAINERS.md)
- Code of conduct: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) · support: [`SUPPORT.md`](SUPPORT.md)
- Examples: [`examples/`](examples/)

## License

Apache-2.0. Copyright 2026 Borgels Olsen Holding ApS (VAT DK39700425).
