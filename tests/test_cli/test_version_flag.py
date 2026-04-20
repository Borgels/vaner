from __future__ import annotations

from typer.testing import CliRunner

from vaner import __version__
from vaner.cli.commands.app import app


def test_version_flag_outputs_installed_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"vaner {__version__}" in result.stdout
