# Editor Proxy — Task 1: Server Implementation
# Issue: #31 (part 1/3)
# Run with: python work.py --plan tasks/proxy_server.md --yes

# ── Task: Implement VanerProxy server ─────────────────────────────────────────

Create apps/vaner-daemon/src/vaner_daemon/proxy/__init__.py with:
# Vaner proxy package

Create apps/vaner-daemon/src/vaner_daemon/proxy/server.py with VanerProxy class:

Requirements:
1. Listen on localhost:11435 (one above Ollama's 11434)
2. For POST /api/chat and POST /v1/chat/completions:
   - Read request body as JSON
   - Call list_artefacts(kind='file_summary') from vaner_tools.artefact_store
   - Take first 5 results by generated_at DESC
   - Format context: "## Vaner context\n" + "\n---\n".join(f"### {a.source_path}\n{a.content}" for a in top5) + "\n---"
   - Inject as first message with role="system"
   - Forward to upstream (default http://localhost:11434)
   - Stream response back
3. All other requests: transparent passthrough
4. If list_artefacts returns empty or raises: pass through unmodified
5. Upstream timeout: 120 seconds

Class VanerProxy:
    def __init__(self, host="127.0.0.1", port=11435, upstream="http://localhost:11434", timeout=120)
    async def start(self) -> None
    async def stop(self) -> None
    def is_running(self) -> bool

Add aiohttp to apps/vaner-daemon/pyproject.toml dependencies if not present.
Install: apps/vaner-daemon/.venv/bin/pip install aiohttp -q

Write tests in apps/vaner-daemon/tests/test_proxy.py:
- test_make_context_string: verify formatting with 2 artefacts
- test_make_context_string_top5_limit: 10 artefacts → only 5 in output
- test_inject_context_no_system_message: adds system message if none exists
- test_inject_context_prepends_to_existing_system: prepends to existing system message
- test_proxy_passthrough_when_no_artefacts: empty list → pass through unchanged

Run: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/test_proxy.py -v
Run: apps/vaner-daemon/.venv/bin/python -m ruff check apps/vaner-daemon/src/vaner_daemon/proxy/ --ignore E501,D,T201,ANN

If all pass: report "Task 1 complete — proxy server ready"
