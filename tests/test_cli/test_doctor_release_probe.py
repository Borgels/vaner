from __future__ import annotations

import json

from typer.testing import CliRunner

from vaner.cli.commands import app

runner = CliRunner()


def test_doctor_release_probe_reports_outdated_install(temp_repo, monkeypatch) -> None:
    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        """
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen2.5-coder:7b"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("VANER_DOCTOR_CHECK_UPDATES", "1")
    monkeypatch.setenv("VANER_SKIP_MCP_BOOT_PROBE", "1")
    config = app.load_config(temp_repo)
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
            "inotify_headroom_pct": 90.0,
            "inotify": {"ok": True, "detail": "headroom ok", "fix": ""},
            "repo_root_sensible": True,
            "repo_root_detail": str(temp_repo),
            "repo_root_fix": "",
            "cli_up_to_date": False,
            "cli_update_detail": "installed=0.4.0 latest=999.0.0",
            "scenario_counts": {"fresh": 0, "recent": 0, "stale": 0, "total": 0},
        },
    )
    monkeypatch.setattr(app, "_detect_local_runtime", lambda: {"detected": False})

    result = runner.invoke(app.app, ["doctor", "--path", str(temp_repo), "--json"])
    payload = json.loads(result.stdout)
    release_probe = next(item for item in payload["checks"] if item["name"] == "cli_up_to_date")
    assert release_probe["ok"] is False
    assert "pipx upgrade vaner" in release_probe["fix"]
