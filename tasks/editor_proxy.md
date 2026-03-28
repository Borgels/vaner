# Editor Integration — VS Code / Cursor Proxy
# Issue: #31
# Run with: python work.py --plan tasks/editor_proxy.md --yes
# NOTE: This plan has a review checkpoint before the final commit (Task 4).
# After Task 3, stop and review before proceeding.

# ── Task 1: Proxy server skeleton ───────────────────────────────────────────
Read apps/vaner-daemon/src/vaner_daemon/daemon.py, apps/vaner-daemon/src/vaner_daemon/state_engine.py, and apps/vaner-daemon/src/vaner_daemon/preparation_engine/engine.py carefully.

Create apps/vaner-daemon/src/vaner_daemon/proxy/__init__.py (empty) and apps/vaner-daemon/src/vaner_daemon/proxy/server.py.

The proxy is a lightweight HTTP server (use Python's built-in http.server or aiohttp if already installed — check pyproject.toml first, prefer built-in) that:
1. Listens on localhost:PORT (configurable, default 11435 — one above Ollama's 11434)
2. Forwards all requests to an upstream URL (configurable, default http://localhost:11434)
3. For POST /api/chat and POST /v1/chat/completions only: intercept the request body, inject vaner context, then forward the modified request
4. All other paths: transparent passthrough (no modification)

The context injection for chat requests:
- Read the current context snapshot from the StateEngine (via a shared reference, not a new subprocess)
- Read the top 5 artifacts from the SQLite store matching the active_files in the snapshot
- Prepend a system message (or augment the first system message if present) with the artifact summaries
- Format: "## Vaner context\n{artifact summaries}\n---"
- If no artifacts available or StateEngine not running: pass through unmodified (never block the request)

Class structure:
- VanerProxy(host, port, upstream_url, state_engine: StateEngine | None)
- start_async(loop) — starts the server in background
- stop() — shuts down cleanly

Add to DaemonConfig: proxy_enabled: bool = True, proxy_port: int = 11435, proxy_upstream: str = "http://localhost:11434"

Write tests in apps/vaner-daemon/tests/test_proxy.py:
- test_proxy_passthrough_non_chat: GET /health → forwarded unchanged (mock upstream)
- test_proxy_injects_context_in_chat: POST /api/chat with messages → system message prepended with artifact content (mock StateEngine + artefact store)
- test_proxy_passthrough_when_no_context: StateEngine has no active files → request unchanged
- test_proxy_handles_upstream_down: upstream not reachable → returns 502 with clear error message

Use pytest-asyncio for async tests.
Run: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/test_proxy.py -v
Run: apps/vaner-daemon/.venv/bin/python -m ruff check apps/vaner-daemon/src/vaner_daemon/proxy/ --ignore E501,D,T201,ANN
Fix all failures.

# ── Task 2: Wire proxy into daemon ──────────────────────────────────────────
Read apps/vaner-daemon/src/vaner_daemon/daemon.py carefully.

Add proxy startup/shutdown to VanerDaemon:
1. Import VanerProxy from vaner_daemon.proxy.server
2. Add self._proxy: VanerProxy | None = None
3. In start(), after preparation engine starts, if self._config.proxy_enabled: create and start proxy, passing self._state_engine
4. In stop(), stop proxy before other components
5. In daemon_status(), add proxy_running: bool and proxy_port: int to the returned dict
6. Update vaner.py daemon status output to show proxy status

Add aiohttp to pyproject.toml dependencies if you chose aiohttp in Task 1. Run: apps/vaner-daemon/.venv/bin/pip install -e apps/vaner-daemon/ -q to install.

Run the daemon briefly to check it starts cleanly:
apps/vaner-daemon/.venv/bin/python vaner.py daemon start && sleep 2 && apps/vaner-daemon/.venv/bin/python vaner.py daemon status && apps/vaner-daemon/.venv/bin/python vaner.py daemon stop

Run: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ -q
Fix any failures.

# ── Task 3: vaner proxy config command + README ─────────────────────────────
Read vaner.py.

Add subcommand: python vaner.py proxy config
This prints configuration instructions for the editor:
- For Cursor: "Set API Base URL to http://localhost:11435"
- For VS Code (Continue): "Set 'apiBase' to 'http://localhost:11435' in config.json"
- Shows current proxy status (running/stopped, port, upstream)
- Shows how many artifacts are in cache

Create docs/editor-integration.md with:
- What vaner proxy does (intercepts → injects context → forwards)
- Setup instructions for Cursor, VS Code (Continue extension), any OpenAI-compatible client
- How to verify it's working (curl example)
- Troubleshooting (upstream down, no context injected, port conflict)

Run: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ -q

# ── REVIEW CHECKPOINT ────────────────────────────────────────────────────────
# At this point, stop. The proxy is built and tested but not committed.
# Manual review recommended before committing editor integration.
# Check: does the proxy actually intercept a curl request correctly?
# Test: curl -X POST http://localhost:11435/api/chat -d '{"messages":[{"role":"user","content":"hello"}]}'
# If it works end-to-end, proceed to Task 4.

# ── Task 4: Commit ──────────────────────────────────────────────────────────
Run full suite: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ libs/vaner-runtime/tests/ libs/vaner-tools/tests/ -q
If all pass: cd /home/abo/repos/Vaner && git add apps/vaner-daemon/ vaner.py docs/ && git commit -m "feat(proxy): editor integration via local OpenAI-compatible proxy

- VanerProxy: HTTP proxy on localhost:11435 → upstream Ollama/OpenAI
- Intercepts POST /api/chat and /v1/chat/completions
- Injects top-5 artifact summaries as system message prefix
- Transparent passthrough when no context available (never blocks)
- Wired into VanerDaemon: start/stop with daemon lifecycle
- DaemonConfig: proxy_enabled, proxy_port, proxy_upstream
- vaner proxy config: prints editor setup instructions
- docs/editor-integration.md: setup guide for Cursor and VS Code

Closes #31 (editor integration)

Tests: all passing" && git push origin develop
