# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx

from vaner.cli.commands.daemon import (
    COCKPIT_PROCESS,
    DAEMON_PROCESS,
    clear_pid,
    log_path,
    process_status,
    runtime_dir,
    write_pid,
)
from vaner.cli.commands.init import init_repo, write_mcp_configs
from vaner.daemon.preflight import check_inotify_budget, check_ports, check_repo_root


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stop_pid(pid: int, timeout_seconds: float = 5.0) -> bool:
    if not _is_pid_running(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _is_pid_running(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return not _is_pid_running(pid)


def _wait_for_health(cockpit_url: str, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    health_url = f"{cockpit_url.rstrip('/')}/health"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(health_url, timeout=1.0)
            if response.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _ensure_initialized(repo_root: Path) -> None:
    config_path = repo_root / ".vaner" / "config.toml"
    if config_path.exists():
        return
    init_repo(repo_root)
    try:
        write_mcp_configs(repo_root)
    except Exception:
        pass


def _spawn_process(command: list[str], logfile: Path) -> subprocess.Popen[bytes]:
    logfile.parent.mkdir(parents=True, exist_ok=True)
    log_handle = logfile.open("a", encoding="utf-8")
    process = subprocess.Popen(
        command,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_handle.close()
    return process


def run_up(
    repo_root: Path,
    *,
    host: str,
    port: int,
    mcp_sse_port: int,
    interval_seconds: int,
    open_browser: bool,
    force: bool = False,
) -> dict[str, object]:
    root_check = check_repo_root(repo_root, force=force)
    if not root_check.get("ok"):
        raise RuntimeError(str(root_check.get("fix") or root_check.get("detail")))
    inotify_check = check_inotify_budget(repo_root)
    _ensure_initialized(repo_root)
    ports_check = check_ports(host, port, mcp_sse_port)
    chosen_port = int(ports_check.get("cockpit_port", port))
    runtime_dir(repo_root).mkdir(parents=True, exist_ok=True)
    daemon_state = process_status(repo_root, DAEMON_PROCESS)
    cockpit_state = process_status(repo_root, COCKPIT_PROCESS)
    if daemon_state["running"] and cockpit_state["running"]:
        return {
            "started": False,
            "reattached": True,
            "daemon_pid": daemon_state["pid"],
            "cockpit_pid": cockpit_state["pid"],
        }
    if daemon_state["running"] and not cockpit_state["running"]:
        clear_pid(repo_root, COCKPIT_PROCESS)
    if cockpit_state["running"] and not daemon_state["running"]:
        clear_pid(repo_root, DAEMON_PROCESS)

    launcher = [sys.executable, "-m", "vaner.cli.main", "daemon"]
    daemon_process = _spawn_process(
        launcher
        + [
            "run-forever",
            "--path",
            str(repo_root),
            "--interval-seconds",
            str(interval_seconds),
        ],
        log_path(repo_root, DAEMON_PROCESS),
    )
    write_pid(repo_root, DAEMON_PROCESS, daemon_process.pid)
    cockpit_process = _spawn_process(
        launcher
        + [
            "serve-http",
            "--path",
            str(repo_root),
            "--host",
            host,
            "--port",
            str(chosen_port),
        ],
        log_path(repo_root, COCKPIT_PROCESS),
    )
    write_pid(repo_root, COCKPIT_PROCESS, cockpit_process.pid)

    cockpit_url = f"http://{host}:{chosen_port}"
    ready = _wait_for_health(cockpit_url)
    if open_browser:
        webbrowser.open(f"{cockpit_url}/")

    return {
        "started": True,
        "reattached": False,
        "ready": ready,
        "cockpit_url": cockpit_url,
        "inotify": inotify_check,
        "ports": ports_check,
        "daemon_pid": daemon_process.pid,
        "cockpit_pid": cockpit_process.pid,
    }


def run_down(repo_root: Path) -> dict[str, object]:
    daemon_state = process_status(repo_root, DAEMON_PROCESS)
    cockpit_state = process_status(repo_root, COCKPIT_PROCESS)

    stopped_daemon = False
    stopped_cockpit = False
    if daemon_state["pid"]:
        stopped_daemon = _stop_pid(int(daemon_state["pid"]))
    if cockpit_state["pid"]:
        stopped_cockpit = _stop_pid(int(cockpit_state["pid"]))
    clear_pid(repo_root, DAEMON_PROCESS)
    clear_pid(repo_root, COCKPIT_PROCESS)

    return {
        "daemon": {"pid": daemon_state["pid"], "stopped": stopped_daemon or not daemon_state["running"]},
        "cockpit": {"pid": cockpit_state["pid"], "stopped": stopped_cockpit or not cockpit_state["running"]},
    }
