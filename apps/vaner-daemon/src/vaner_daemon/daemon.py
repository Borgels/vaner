"""Vaner daemon: main process managing all background components.

Start:  vaner daemon start
Stop:   vaner daemon stop  (sends SIGTERM)
Status: vaner daemon status
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("vaner.daemon")


class VanerDaemon:
    """Manages all daemon components: event collector, state engine, socket listener."""

    def __init__(self, repo_path: Path) -> None:
        from vaner_daemon.config import DaemonConfig
        from vaner_daemon.event_collector import EventCollector
        from vaner_daemon.state_engine import StateEngine

        self._config = DaemonConfig.load(repo_path)
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._event_collector: EventCollector | None = None
        self._state_engine: StateEngine | None = None
        self._preparation_engine = None
        self._running = False
        self._pid_file = repo_path / ".vaner" / "daemon.pid"
        self._log_file = repo_path / ".vaner" / "daemon.log"
        self._sock_file = repo_path / ".vaner" / "daemon.sock"
        self._socket_server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Start all components. Called from main()."""
        from vaner_daemon.event_collector import EventCollector
        from vaner_daemon.state_engine import StateEngine

        self._write_pid()
        self._setup_logging()
        self._setup_signal_handlers()

        self._state_engine = StateEngine(self._config, self._event_queue)
        await self._state_engine.start()

        loop = asyncio.get_running_loop()
        self._event_collector = EventCollector(self._config, self._event_queue)
        self._event_collector.start(loop)

        # Start unix socket listener for git hooks
        await self._start_socket_listener()

        # Start preparation engine (background artifact generation)
        from vaner_daemon.preparation_engine.engine import PreparationEngine
        self._preparation_engine = PreparationEngine(
            repo_root=self._config.repo_path,
            state_engine=self._state_engine,
            loop=loop,
        )
        self._preparation_engine.start()
        self._preparation_engine.recover_in_progress_runs()

        self._running = True
        logger.info("Vaner daemon started (pid=%d, repo=%s)", os.getpid(), self._config.repo_path)

        # Keep running until signal received
        while self._running:
            await asyncio.sleep(1.0)

    async def stop(self) -> None:
        self._running = False

        if self._socket_server:
            self._socket_server.close()
            try:
                await self._socket_server.wait_closed()
            except Exception:
                pass
            if self._sock_file.exists():
                self._sock_file.unlink(missing_ok=True)

        if self._preparation_engine:
            self._preparation_engine.stop()

        if self._event_collector:
            self._event_collector.stop()

        if self._state_engine:
            await self._state_engine.stop()

        self._remove_pid()
        logger.info("Vaner daemon stopped")

    def _write_pid(self) -> None:
        self._pid_file.parent.mkdir(parents=True, exist_ok=True)
        self._pid_file.write_text(str(os.getpid()))

    def _remove_pid(self) -> None:
        if self._pid_file.exists():
            try:
                self._pid_file.unlink()
            except Exception:
                pass

    def _setup_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

    def _setup_logging(self) -> None:
        from vaner_runtime.logging import configure_logging
        configure_logging(dev_mode=False, log_file=str(self._log_file))

    async def _start_socket_listener(self) -> None:
        """Listen on .vaner/daemon.sock for JSON events from git hooks."""
        sock_path = str(self._sock_file)

        # Remove stale socket
        if self._sock_file.exists():
            self._sock_file.unlink(missing_ok=True)

        try:
            self._socket_server = await asyncio.start_unix_server(
                self._handle_socket_client, path=sock_path
            )
            logger.info("Socket listener started at %s", sock_path)
        except Exception as exc:
            logger.warning("Failed to start socket listener: %s", exc)

    async def _handle_socket_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single connection from a git hook."""
        from vaner_daemon.event_collector import EventKind, VanerEvent

        try:
            data = await asyncio.wait_for(reader.read(4096), timeout=2.0)
            if not data:
                return
            payload = json.loads(data.decode().strip())
            kind_str = payload.get("kind", "")
            branch = payload.get("branch", "")
            from_branch = payload.get("from_branch", "")

            kind_map = {
                "git_commit": EventKind.GIT_COMMIT,
                "git_checkout": EventKind.GIT_CHECKOUT,
                "git_branch_switch": EventKind.GIT_BRANCH_SWITCH,
            }
            kind = kind_map.get(kind_str)
            if kind is None:
                logger.warning("Unknown git event kind from hook: %r", kind_str)
                return

            event = VanerEvent(kind=kind, path="", branch=branch, from_branch=from_branch)
            if self._event_collector:
                self._event_collector.inject_git_event(event)
            logger.debug("Received git hook event: kind=%s branch=%s", kind_str, branch)
        except asyncio.TimeoutError:
            logger.debug("Socket client timed out")
        except json.JSONDecodeError as exc:
            logger.warning("Invalid JSON from git hook: %s", exc)
        except Exception as exc:
            logger.warning("Error handling socket client: %s", exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Standalone functions for use by vaner.py
# ---------------------------------------------------------------------------


def daemon_start(repo_path: Path) -> None:
    """Fork and start the daemon in background."""
    log_file = repo_path / ".vaner" / "daemon.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with open(log_file, "a") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "vaner_daemon.daemon", "start", str(repo_path)],
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    # Give it a moment to write the PID file
    pid_file = repo_path / ".vaner" / "daemon.pid"
    for _ in range(20):
        time.sleep(0.1)
        if pid_file.exists():
            break

    logger.info("Daemon started with pid=%s", proc.pid)


def daemon_stop(repo_path: Path) -> None:
    """Send SIGTERM to the daemon process."""
    pid_file = repo_path / ".vaner" / "daemon.pid"
    if not pid_file.exists():
        logger.warning("No PID file found at %s — daemon may not be running", pid_file)
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        # Wait up to 5s for process to exit
        for _ in range(50):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)  # check if still alive
            except ProcessLookupError:
                break  # process gone
        logger.info("Daemon stopped (pid=%d)", pid)
    except (ValueError, FileNotFoundError):
        logger.warning("Could not read PID from %s", pid_file)
    except ProcessLookupError:
        logger.warning("No process with PID from %s — cleaning up", pid_file)
        pid_file.unlink(missing_ok=True)
    except PermissionError as exc:
        logger.error("Permission error stopping daemon: %s", exc)


def daemon_status(repo_path: Path) -> dict:
    """Return running status, pid, uptime, branch, and active files."""
    pid_file = repo_path / ".vaner" / "daemon.pid"
    result: dict = {
        "running": False,
        "pid": None,
        "uptime_seconds": None,
        "branch": "",
        "active_files": [],
    }

    if not pid_file.exists():
        return result

    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, FileNotFoundError):
        return result

    # Check if process is alive
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # Stale PID file
        pid_file.unlink(missing_ok=True)
        return result
    except PermissionError:
        pass  # process exists but we can't signal it — still "running"

    result["running"] = True
    result["pid"] = pid

    # Uptime from PID file mtime
    try:
        mtime = pid_file.stat().st_mtime
        result["uptime_seconds"] = time.time() - mtime
    except Exception:
        pass

    # Read state from SQLite
    db_path = repo_path / ".vaner" / "state.db"
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT path FROM active_files ORDER BY last_touched DESC LIMIT 10"
            ).fetchall()
            result["active_files"] = [r[0] for r in rows]
            conn.close()
        except Exception:
            pass

    # Read branch from git
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            result["branch"] = r.stdout.strip()
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for `python -m vaner_daemon.daemon start <repo_path>`."""
    import argparse
    parser = argparse.ArgumentParser(description="Vaner daemon")
    parser.add_argument("action", choices=["start"], help="Action to perform")
    parser.add_argument("repo_path", type=Path, help="Path to the repository root")
    args = parser.parse_args()

    if args.action == "start":
        daemon = VanerDaemon(args.repo_path)
        asyncio.run(daemon.start())


if __name__ == "__main__":
    main()
