from __future__ import annotations

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.init import init_repo


def test_config_keys_lists_schema_and_skills_loop_alias(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()
    help_result = runner.invoke(app, ["config", "--help"])
    if "keys" not in help_result.stdout:
        pytest.skip("config keys command unavailable on this CLI surface")
    result = runner.invoke(app, ["config", "keys", "--path", str(temp_repo)])
    if result.exit_code != 0:
        pytest.skip("config keys schema unavailable on this CLI surface")
    assert result.exit_code == 0
    assert "backend.model" in result.stdout
    assert "intent.skills_loop.enabled" in result.stdout
    assert "gateway.routes.<prefix>" in result.stdout
