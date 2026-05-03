# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

import pytest

from vaner.cli.commands.daemon import COCKPIT_PROCESS, DAEMON_PROCESS, write_pid
from vaner.cli.commands.supervisor import run_down, run_up

if os.name == "nt":
    pytest.skip("Skip flaky supervisor up/down tests on Windows CI", allow_module_level=True)


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


def test_up_json_flag_emits_canonical_shape(monkeypatch, temp_repo) -> None:
    """`vaner up --json` emits exactly one structured line of stdout
    with the canonical key set, regardless of fresh-start vs reattach.
    The desktop's auto-bring-up reads this to decide whether to flip
    out of `.error` immediately or surface a useful failure detail."""
    import json

    from typer.testing import CliRunner

    from vaner.cli.main import app as cli_app

    monkeypatch.setattr(
        "vaner.cli.commands.app.run_up",
        lambda *_args, **_kwargs: {
            "started": True,
            "reattached": False,
            "ready": True,
            "cockpit_url": "http://127.0.0.1:8473",
            "daemon_pid": 123,
            "cockpit_pid": 124,
            "ports": {"cockpit_port": 8473, "cockpit_changed": False},
            "inotify": {"ok": True},
        },
    )

    result = CliRunner().invoke(cli_app, ["up", "--path", str(temp_repo), "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output.strip())
    assert parsed == {
        "started": True,
        "reattached": False,
        "ready": True,
        "cockpit_url": "http://127.0.0.1:8473",
        "daemon_pid": 123,
        "cockpit_pid": 124,
        "ports": {"cockpit_port": 8473, "cockpit_changed": False},
        "inotify": {"ok": True},
    }


def test_up_json_flag_emits_error_payload_on_failure(monkeypatch, temp_repo) -> None:
    """run_up's RuntimeError surface (e.g. non-repo root + no --force)
    becomes a JSON error payload on stdout with exit code 1, so the
    desktop's bring-up flow can show a real fix-it message instead of
    silently swallowing the failure."""
    import json

    from typer.testing import CliRunner

    from vaner.cli.main import app as cli_app

    def _boom(*_args, **_kwargs):
        raise RuntimeError("repo root looks wrong; pass --force to override")

    monkeypatch.setattr("vaner.cli.commands.app.run_up", _boom)

    result = CliRunner().invoke(cli_app, ["up", "--path", str(temp_repo), "--json"])
    assert result.exit_code == 1
    parsed = json.loads(result.output.strip())
    assert parsed["started"] is False
    assert "repo root looks wrong" in parsed["error"]


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


def test_run_down_stops_stale_same_repo_runtime_pids(monkeypatch, temp_repo) -> None:
    write_pid(temp_repo, DAEMON_PROCESS, 1001)
    write_pid(temp_repo, COCKPIT_PROCESS, 1002)
    stopped: list[int] = []

    monkeypatch.setattr(
        "vaner.cli.commands.daemon._is_pid_running",
        lambda pid: pid in {1001, 1002, 2001},
    )
    monkeypatch.setattr(
        "vaner.cli.commands.supervisor._runtime_pids_for_repo",
        lambda _repo_root: {1001, 1002, 2001},
    )

    def _fake_stop(pid: int, *_args, **_kwargs) -> bool:
        stopped.append(pid)
        return True

    monkeypatch.setattr("vaner.cli.commands.supervisor._stop_pid", _fake_stop)

    payload = run_down(temp_repo)

    assert stopped == [1001, 1002, 2001]
    assert payload["stale"] == {"pids": [2001], "stopped": [2001]}
