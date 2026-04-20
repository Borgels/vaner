# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from vaner import api
from vaner.cli.commands.config import load_config
from vaner.daemon.runner import VanerDaemon

DAEMON_PROCESS = "daemon"
COCKPIT_PROCESS = "cockpit"


def runtime_dir(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "runtime"


def pid_path(repo_root: Path, process_name: str = DAEMON_PROCESS) -> Path:
    return runtime_dir(repo_root) / f"{process_name}.pid"


def log_path(repo_root: Path, process_name: str) -> Path:
    return runtime_dir(repo_root) / "logs" / f"{process_name}.log"


def _read_pid(repo_root: Path, process_name: str = DAEMON_PROCESS) -> int | None:
    path = pid_path(repo_root, process_name)
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


def _tail_log(log_file: Path, max_lines: int = 12) -> str:
    if not log_file.exists():
        return ""
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:]).strip()


def write_pid(repo_root: Path, process_name: str, pid: int) -> None:
    path = pid_path(repo_root, process_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def clear_pid(repo_root: Path, process_name: str) -> None:
    pid_path(repo_root, process_name).unlink(missing_ok=True)


def process_status(repo_root: Path, process_name: str) -> dict[str, object]:
    pid = _read_pid(repo_root, process_name)
    running = bool(pid and _is_pid_running(pid))
    return {
        "name": process_name,
        "pid": pid,
        "running": running,
        "status": f"running (pid={pid})" if running else "stopped",
    }


def start_daemon(repo_root: Path, once: bool = True, interval_seconds: int = 15) -> int:
    runtime_dir(repo_root).mkdir(parents=True, exist_ok=True)
    if once:
        return api.prepare(repo_root)

    existing_pid = _read_pid(repo_root, DAEMON_PROCESS)
    if existing_pid is not None and _is_pid_running(existing_pid):
        return 0

    daemon_log_path = log_path(repo_root, DAEMON_PROCESS)
    daemon_log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = daemon_log_path.open("a", encoding="utf-8")
    try:
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
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_handle.close()
    exit_code: int | None = None
    for _ in range(5):
        time.sleep(0.1)
        exit_code = process.poll()
        if exit_code is not None:
            break
    if exit_code is not None:
        details = _tail_log(daemon_log_path)
        suffix = f"\n{details}" if details else ""
        raise RuntimeError(f"Background daemon failed to start (exit={exit_code}).{suffix}")
    write_pid(repo_root, DAEMON_PROCESS, process.pid)
    return 0


def run_daemon_forever(repo_root: Path, interval_seconds: int = 15) -> None:
    config = load_config(repo_root)
    daemon = VanerDaemon(config)
    asyncio.run(daemon.run_forever(interval_seconds=interval_seconds))


def stop_daemon(repo_root: Path) -> bool:
    pid = _read_pid(repo_root, DAEMON_PROCESS)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    clear_pid(repo_root, DAEMON_PROCESS)
    return True


def daemon_status(repo_root: Path) -> str:
    return str(process_status(repo_root, DAEMON_PROCESS)["status"])
