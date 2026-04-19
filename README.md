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

### 1. Install

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash
```

The installer:

- Detects `uv` or `pipx` and installs `vaner[mcp]` (MCP client support included by default; pass `--no-mcp` to skip).
- Falls back to `git+https://github.com/Borgels/vaner.git` if the PyPI package isn't available yet.
- Asks you to pick a model backend (Ollama, LM Studio, vLLM, OpenAI, Anthropic, OpenRouter, or skip).
- Offers a compute budget (`background` / `balanced` / `dedicated`) and an optional wall-clock cap on ponder time (`--max-session-minutes`).

Non-interactive example (CI, Dockerfile):

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh \
  | bash -s -- --yes --backend-preset openai \
      --backend-api-key-env OPENAI_API_KEY \
      --backend-model gpt-4o \
      --compute-preset background \
      --max-session-minutes 30
```

From source (no installer):

```bash
git clone https://github.com/Borgels/vaner.git
cd vaner
pip install '.[mcp]'
```

Installer source for review: [`scripts/install.sh`](scripts/install.sh).

### 2. Connect your AI client

Vaner exposes context to your agent over [MCP](https://modelcontextprotocol.io/). Pick your client and paste the one-liner:

| Client | One command |
| --- | --- |
| Claude Code | `claude mcp add --transport stdio --scope user vaner -- vaner mcp --path .` |
| Codex CLI   | `codex mcp add vaner -- vaner mcp --path .` |
| Cursor / VS Code / Zed / Windsurf / Continue / Claude Desktop / Cline / Roo | see [docs.vaner.ai/mcp](https://docs.vaner.ai/mcp) |

Or let Vaner write the config file for you:

```bash
vaner init --path .                 # initialize this repo + pick backend interactively
vaner mcp --path .                  # smoke-test the MCP server in stdio mode
```

### 3. Run it

```bash
vaner daemon start --no-once --path .
vaner query "where is auth enforced?" --explain --path .
vaner inspect --last --path .
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
- Code of conduct: `CODE_OF_CONDUCT.md`
- Support channels: `SUPPORT.md`
- Examples: `examples/`

## OpenSSF Best Practices

Vaner is preparing an OpenSSF Best Practices submission.

## License

Apache-2.0. Copyright 2026 Borgels Olsen Holding ApS (VAT DK39700425).
