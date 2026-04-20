from __future__ import annotations

from typer.testing import CliRunner

from vaner.cli.commands import app as app_module
from vaner.cli.commands.app import app


def test_version_flag_outputs_installed_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"vaner {app_module.VERSION}" in result.stdout
