# Editor Proxy — Task 3: CLI & Documentation
# Issue: #31 (part 3/3)
# Run with: python work.py --plan tasks/proxy_cli.md --yes
# Depends on: proxy_daemon.md completed

# ── Task: Add CLI command and documentation ────────────────────────────────

Read vaner.py carefully — understand existing subcommand structure.

Add subparser for "proxy" with action "config":
    python vaner.py proxy config

Output format:
    Vaner Proxy Configuration
    =========================
    Status: running (port 11435) OR stopped
    Upstream: http://localhost:11434
    Artifacts in cache: N

    Editor setup:
    - Cursor: Settings > API Base URL > http://localhost:11435
    - VS Code (Continue): set apiBase to http://localhost:11435 in config.json
    - Any OpenAI-compatible client: set base URL to http://localhost:11435

    Test with:
    curl -s http://localhost:11435/health

Create docs/editor-integration.md with:
- Overview: What the proxy does
- Cursor setup steps
- VS Code + Continue extension setup
- Generic OpenAI-compatible client setup
- Verification: curl example
- Troubleshooting section

Run: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ -q
Run: apps/vaner-daemon/.venv/bin/python -m ruff check apps/vaner-daemon/src/ vaner.py --ignore E501,D,T201,ANN

If all pass:
cd /home/abo/repos/Vaner && git add apps/vaner-daemon/ vaner.py docs/editor-integration.md && git commit -m "feat(proxy): editor integration — local OpenAI-compatible proxy on :11435

- VanerProxy: aiohttp server proxying localhost:11435 -> Ollama :11434
- Intercepts POST /api/chat + /v1/chat/completions, injects top-5 artifact context
- Transparent passthrough for all other paths and when no artifacts available
- DaemonConfig: proxy_enabled, proxy_port, proxy_upstream fields
- VanerDaemon: proxy starts/stops with daemon lifecycle
- vaner proxy config: prints editor setup instructions
- docs/editor-integration.md: Cursor + VS Code setup guide

Closes #31 (editor integration)

Tests: all passing" && git push origin develop

If commit succeeds: report "Task 3 complete — editor proxy delivered 🎉"
