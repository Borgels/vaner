# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

from vaner.cli.commands.daemon import COCKPIT_PROCESS, DAEMON_PROCESS, write_pid
from vaner.cli.commands.supervisor import run_down, run_up


def test_run_up_writes_pid_files_and_run_down_cleans(monkeypatch, temp_repo) -> None:
    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        """
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen3.5:35b"
""".strip(),
        encoding="utf-8",
    )

    pid_seed = {"value": 1000}

    class _Proc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def _fake_spawn(_cmd, _logfile):
        pid_seed["value"] += 1
        return _Proc(pid_seed["value"])

    monkeypatch.setattr("vaner.cli.commands.supervisor._spawn_process", _fake_spawn)
    monkeypatch.setattr("vaner.cli.commands.supervisor._wait_for_health", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("vaner.cli.commands.supervisor.check_repo_root", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(
        "vaner.cli.commands.supervisor.check_ports",
        lambda *_args, **_kwargs: {"cockpit_port": 8473, "mcp_sse_port": 8472, "cockpit_changed": False},
    )
    monkeypatch.setattr(
        "vaner.cli.commands.supervisor.check_inotify_budget",
        lambda *_args, **_kwargs: {"ok": True, "detail": "headroom ok"},
    )
    monkeypatch.setattr("vaner.cli.commands.supervisor._stop_pid", lambda *_args, **_kwargs: True)

    payload = run_up(temp_repo, host="127.0.0.1", port=8473, mcp_sse_port=8472, interval_seconds=15, open_browser=False)
    assert payload["started"] is True
    assert (temp_repo / ".vaner" / "runtime" / "daemon.pid").exists()
    assert (temp_repo / ".vaner" / "runtime" / "cockpit.pid").exists()

    down_payload = run_down(temp_repo)
    assert down_payload["daemon"]["stopped"] is True
    assert down_payload["cockpit"]["stopped"] is True
    assert not (temp_repo / ".vaner" / "runtime" / "daemon.pid").exists()
    assert not (temp_repo / ".vaner" / "runtime" / "cockpit.pid").exists()


def test_run_up_is_idempotent_when_processes_already_running(monkeypatch, temp_repo) -> None:
    write_pid(temp_repo, DAEMON_PROCESS, os.getpid())
    write_pid(temp_repo, COCKPIT_PROCESS, os.getpid())
    monkeypatch.setattr("vaner.cli.commands.supervisor.check_repo_root", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(
        "vaner.cli.commands.supervisor.check_inotify_budget",
        lambda *_args, **_kwargs: {"ok": True, "detail": "headroom ok"},
    )
    payload = run_up(temp_repo, host="127.0.0.1", port=8473, mcp_sse_port=8472, interval_seconds=15, open_browser=False)
    assert payload["reattached"] is True
    assert payload["started"] is False
