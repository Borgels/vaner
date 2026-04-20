from __future__ import annotations

from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.init import init_repo


def test_config_keys_lists_schema_and_skills_loop_alias(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()
    result = runner.invoke(app, ["config", "keys", "--path", str(temp_repo)])
    assert result.exit_code == 0
    assert "backend.model" in result.stdout
    assert "intent.skills_loop.enabled" in result.stdout
    assert "gateway.routes.<prefix>" in result.stdout
