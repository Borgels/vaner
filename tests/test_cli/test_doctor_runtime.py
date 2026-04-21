from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.init import init_repo


def test_doctor_reports_missing_python_deps(temp_repo: Path, monkeypatch) -> None:
    if not hasattr(__import__("vaner.cli.commands.app", fromlist=["app"]), "_python_dep_checks"):
        pytest.skip("python dependency doctor checks unavailable on this CLI surface")
    runner = CliRunner()
    init_repo(temp_repo)

    async def _fake_smoke(_repo_root: Path) -> dict[str, object]:
        return {"ok": True, "detail": "ok"}

    monkeypatch.setattr(
        "vaner.cli.commands.app._python_dep_checks",
        lambda: [
            {"module": "sentence_transformers", "package": "sentence-transformers", "ok": False},
            {"module": "torch", "package": "torch", "ok": True},
        ],
    )
    monkeypatch.setattr("vaner.cli.commands.app._detect_local_runtime", lambda: {"detected": True, "url": "http://127.0.0.1:11434"})
    monkeypatch.setattr("vaner.mcp.server.run_smoke_probe", _fake_smoke)

    result = runner.invoke(app, ["doctor", "--path", str(temp_repo), "--json"])
    payload = json.loads(result.stdout)
    dep_check = next(item for item in payload["checks"] if item["name"] == "python_deps")
    assert dep_check["ok"] is False
    assert "sentence-transformers" in dep_check["detail"]
    assert "doctor --fix" in dep_check["fix"]


def test_doctor_fix_attempts_install(temp_repo: Path, monkeypatch) -> None:
    if not hasattr(__import__("vaner.cli.commands.app", fromlist=["app"]), "_python_dep_checks"):
        pytest.skip("python dependency doctor checks unavailable on this CLI surface")
    runner = CliRunner()
    init_repo(temp_repo)

    async def _fake_smoke(_repo_root: Path) -> dict[str, object]:
        return {"ok": True, "detail": "ok"}

    states = [
        [
            {"module": "sentence_transformers", "package": "sentence-transformers", "ok": False},
            {"module": "torch", "package": "torch", "ok": True},
        ],
        [
            {"module": "sentence_transformers", "package": "sentence-transformers", "ok": True},
            {"module": "torch", "package": "torch", "ok": True},
        ],
    ]

    def _checks() -> list[dict[str, object]]:
        if states:
            return states.pop(0)
        return [
            {"module": "sentence_transformers", "package": "sentence-transformers", "ok": True},
            {"module": "torch", "package": "torch", "ok": True},
        ]

    monkeypatch.setattr("vaner.cli.commands.app._python_dep_checks", _checks)
    monkeypatch.setattr("vaner.cli.commands.app._detect_local_runtime", lambda: {"detected": True, "url": "http://127.0.0.1:11434"})
    monkeypatch.setattr("vaner.mcp.server.run_smoke_probe", _fake_smoke)
    monkeypatch.setattr("vaner.cli.commands.app._install_python_packages", lambda packages: (True, f"installed {packages}"))

    result = runner.invoke(app, ["doctor", "--path", str(temp_repo), "--json", "--fix"])
    payload = json.loads(result.stdout)
    autofix = next(item for item in payload["checks"] if item["name"] == "python_deps_autofix")
    after = next(item for item in payload["checks"] if item["name"] == "python_deps_after_fix")
    assert autofix["ok"] is True
    assert after["ok"] is True
