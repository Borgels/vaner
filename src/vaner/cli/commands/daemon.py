# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

from vaner import api
from vaner.cli.commands.config import load_config
from vaner.daemon.runner import VanerDaemon


def pid_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "runtime" / "daemon.pid"


def _read_pid(repo_root: Path) -> int | None:
    path = pid_path(repo_root)
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    try:
        return int(raw)
    except ValueError:
        return None


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_daemon(repo_root: Path, once: bool = True, interval_seconds: int = 15) -> int:
    pid_file = pid_path(repo_root)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    if once:
        return api.prepare(repo_root)

    existing_pid = _read_pid(repo_root)
    if existing_pid is not None and _is_pid_running(existing_pid):
        return 0

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "vaner.cli.main",
            "daemon",
            "run-forever",
            "--path",
            str(repo_root),
            "--interval-seconds",
            str(interval_seconds),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    return 0


def run_daemon_forever(repo_root: Path, interval_seconds: int = 15) -> None:
    config = load_config(repo_root)
    daemon = VanerDaemon(config)
    asyncio.run(daemon.run_forever(interval_seconds=interval_seconds))


def stop_daemon(repo_root: Path) -> bool:
    pid = _read_pid(repo_root)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    pid_path(repo_root).unlink(missing_ok=True)
    return True


def daemon_status(repo_root: Path) -> str:
    pid = _read_pid(repo_root)
    if pid is None:
        return "stopped"
    return f"running (pid={pid})" if _is_pid_running(pid) else "stopped"
