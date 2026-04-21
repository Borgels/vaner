from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from vaner.cli.commands import app

runner = CliRunner()

if os.name == "nt":
    pytest.skip("doctor command tests are flaky on Windows CI", allow_module_level=True)


def _write_basic_config(temp_repo) -> None:  # noqa: ANN001
    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        """
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen3.5:35b"
""".strip(),
        encoding="utf-8",
    )


def test_doctor_daemon_running_fails_when_cockpit_unreachable(temp_repo, monkeypatch) -> None:
    _write_basic_config(temp_repo)
    config = app.load_config(temp_repo)

    monkeypatch.setattr(
        app,
        "runtime_snapshot",
        lambda *_args, **_kwargs: {
            "repo_root": str(temp_repo),
            "config": config,
            "daemon": {"status": "stopped"},
            "cockpit_process": {"status": "stopped"},
            "daemon_pid_alive": False,
            "cockpit_pid_alive": False,
            "cockpit_reachable": False,
            "cockpit_detail": "connection refused",
            "backend_reachable": True,
            "backend_detail": "ok",
            "inotify_headroom_pct": 80.0,
            "inotify": {"ok": True, "detail": "headroom ok", "fix": ""},
            "repo_root_sensible": True,
            "repo_root_detail": str(temp_repo),
            "repo_root_fix": "",
            "cli_up_to_date": True,
            "cli_update_detail": "installed=current",
            "scenario_counts": {"fresh": 0, "recent": 0, "stale": 0, "total": 0},
        },
    )
    monkeypatch.setenv("VANER_SKIP_MCP_BOOT_PROBE", "1")
    monkeypatch.setattr(app, "_detect_local_runtime", lambda: {"detected": False})

    result = runner.invoke(app.app, ["doctor", "--path", str(temp_repo), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    daemon_running = next(item for item in payload["checks"] if item["name"] == "daemon_running")
    assert daemon_running["ok"] is False
    assert "vaner daemon start" in daemon_running["fix"]


def test_doctor_ollama_model_pulled_fails_for_missing_model(temp_repo, monkeypatch) -> None:
    _write_basic_config(temp_repo)
    config = app.load_config(temp_repo)
    (temp_repo / ".cursor").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".cursor" / "mcp.json").write_text(
        '{"mcpServers":{"vaner":{"command":"vaner","args":["mcp","--path","."]}}}',
        encoding="utf-8",
    )

    class _Resp:
        def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
            self.status_code = status_code
            self._payload = payload or {}
            self.text = json.dumps(self._payload)

        def json(self) -> dict[str, object]:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise app.httpx.HTTPStatusError("error", request=None, response=None)

    def _fake_get(url: str, *args, **kwargs):  # noqa: ANN001
        if url.endswith("/health") or url.endswith("/status"):
            return _Resp(200, {"status": "ok"})
        if url.endswith("/api/tags"):
            return _Resp(200, {"models": [{"name": "qwen2.5-coder:32b"}]})
        return _Resp(200)

    monkeypatch.setenv("VANER_SKIP_MCP_BOOT_PROBE", "1")
    monkeypatch.setattr(
        app, "_detect_local_runtime", lambda: {"detected": True, "name": "ollama", "url": "http://127.0.0.1:11434/api/tags"}
    )
    monkeypatch.setattr(
        app,
        "runtime_snapshot",
        lambda *_args, **_kwargs: {
            "repo_root": str(temp_repo),
            "config": config,
            "daemon": {"status": "running (pid=1)"},
            "cockpit_process": {"status": "running (pid=2)"},
            "daemon_pid_alive": True,
            "cockpit_pid_alive": True,
            "cockpit_reachable": True,
            "cockpit_detail": "status=200",
            "backend_reachable": True,
            "backend_detail": "status=200",
            "inotify_headroom_pct": 80.0,
            "inotify": {"ok": True, "detail": "headroom ok", "fix": ""},
            "repo_root_sensible": True,
            "repo_root_detail": str(temp_repo),
            "repo_root_fix": "",
            "cli_up_to_date": True,
            "cli_update_detail": "installed=current",
            "scenario_counts": {"fresh": 0, "recent": 0, "stale": 0, "total": 0},
        },
    )
    monkeypatch.setattr(app.httpx, "get", _fake_get)
    monkeypatch.setattr(app.shutil, "which", lambda *_args, **_kwargs: "/usr/local/bin/vaner")

    result = runner.invoke(app.app, ["doctor", "--path", str(temp_repo), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    daemon_running = next(item for item in payload["checks"] if item["name"] == "daemon_running")
    assert daemon_running["ok"] is True
    model_check = next(item for item in payload["checks"] if item["name"] == "ollama_model_pulled")
    assert model_check["ok"] is False
    assert "ollama pull qwen3.5:35b" in model_check["fix"]
