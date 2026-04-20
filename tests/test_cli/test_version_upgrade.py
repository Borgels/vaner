# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typer.testing import CliRunner

from vaner.cli.commands import app as app_module
from vaner.cli.commands.app import app

runner = CliRunner()


def test_version_alias_prints_installed_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"vaner {app_module.VERSION}" in result.stdout


def test_help_alias_prints_root_help() -> None:
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "Commands" in result.stdout


def test_upgrade_refuses_downgrade_by_default(monkeypatch) -> None:
    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"info": {"version": "0.1.0"}}

    monkeypatch.setattr(app_module.httpx, "get", lambda *args, **kwargs: _Resp())
    result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 1
    assert "Refusing downgrade" in result.stdout


def test_upgrade_uses_pinned_latest_version(monkeypatch) -> None:
    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"info": {"version": "0.3.0"}}

    captured: dict[str, object] = {}

    class _Completed:
        returncode = 0

    def _fake_run(cmd, check=False):  # noqa: ANN001
        captured["cmd"] = cmd
        return _Completed()

    monkeypatch.setattr(app_module.httpx, "get", lambda *args, **kwargs: _Resp())
    monkeypatch.setattr(app_module.shutil, "which", lambda _name: "/usr/bin/pipx")
    monkeypatch.setattr(app_module.subprocess, "run", _fake_run)

    result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 0
    assert "Upgrading via pipx..." in result.stdout
    assert "Upgrade complete." in result.stdout
    assert captured["cmd"] == ["/usr/bin/pipx", "install", "--force", "vaner==0.3.0"]
