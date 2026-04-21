# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from vaner.cli.commands import app

runner = CliRunner()

if os.name == "nt":
    pytest.skip("doctor/status consistency test is flaky on Windows CI", allow_module_level=True)


def test_status_and_doctor_share_runtime_snapshot_when_daemon_down(temp_repo, monkeypatch) -> None:
    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        """
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen3.5:35b"
""".strip(),
        encoding="utf-8",
    )
    config = app.load_config(temp_repo)
    snapshot = {
        "repo_root": str(temp_repo),
        "config": config,
        "daemon": {"status": "stopped"},
        "cockpit_process": {"status": "stopped"},
        "daemon_pid_alive": False,
        "cockpit_pid_alive": False,
        "cockpit_reachable": False,
        "cockpit_detail": "connection refused",
        "backend_reachable": True,
        "backend_detail": "status=200",
        "inotify_headroom_pct": 42.0,
        "inotify": {"ok": False, "detail": "headroom low", "fix": "sysctl ..."},
        "repo_root_sensible": True,
        "repo_root_detail": str(temp_repo),
        "repo_root_fix": "",
        "cli_up_to_date": True,
        "cli_update_detail": "installed=current latest=current",
        "scenario_counts": {"fresh": 0, "recent": 0, "stale": 0, "total": 0},
    }
    monkeypatch.setenv("VANER_SKIP_MCP_BOOT_PROBE", "1")
    monkeypatch.setattr(app, "_detect_local_runtime", lambda: {"detected": False})
    monkeypatch.setattr(app, "runtime_snapshot", lambda *_args, **_kwargs: snapshot)

    status_result = runner.invoke(app.app, ["status", "--path", str(temp_repo), "--json"])
    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["daemon"] == "stopped"

    doctor_result = runner.invoke(app.app, ["doctor", "--path", str(temp_repo), "--json"])
    assert doctor_result.exit_code == 1
    doctor_payload = json.loads(doctor_result.stdout)
    daemon_check = next(item for item in doctor_payload["checks"] if item["name"] == "daemon_running")
    assert daemon_check["ok"] is False
