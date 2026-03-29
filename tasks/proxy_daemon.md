# Editor Proxy — Task 2: Daemon Integration  
# Issue: #31 (part 2/3)
# Run with: python work.py --plan tasks/proxy_daemon.md --yes
# Depends on: proxy_server.md completed

# ── Task: Wire proxy into daemon lifecycle ──────────────────────────────────

Read apps/vaner-daemon/src/vaner_daemon/daemon.py and config.py carefully.

Add to DaemonConfig in apps/vaner-daemon/src/vaner_daemon/config.py:
    proxy_enabled: bool = True
    proxy_port: int = 11435
    proxy_upstream: str = "http://localhost:11434"

Update apps/vaner-daemon/src/vaner_daemon/daemon.py:
- Add import: from vaner_daemon.proxy.server import VanerProxy
- Add to VanerDaemon.__init__: self._proxy: VanerProxy | None = None
- In VanerDaemon.start(), after preparation engine starts:
    if self._config.proxy_enabled:
        self._proxy = VanerProxy(port=self._config.proxy_port, upstream=self._config.proxy_upstream)
        asyncio.create_task(self._proxy.start())
        logger.info("Proxy started on port %d", self._config.proxy_port)
- In VanerDaemon.stop(), before other stops:
    if self._proxy:
        await self._proxy.stop()

Update daemon_status() function to include:
    proxy_running: bool (is proxy enabled and started)
    proxy_port: int

Run: apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ -q
Run: apps/vaner-daemon/.venv/bin/python -m ruff check apps/vaner-daemon/src/ --ignore E501,D,T201,ANN

Fix any test failures.

If all pass: report "Task 2 complete — proxy integrated with daemon"
