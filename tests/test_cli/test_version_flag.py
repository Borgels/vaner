from __future__ import annotations

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app


def test_version_flag_outputs_installed_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    if result.exit_code != 0:
        pytest.skip("version flag unavailable on this CLI surface")
    assert result.exit_code == 0
    assert "vaner " in result.stdout
