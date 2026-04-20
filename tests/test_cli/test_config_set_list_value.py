from __future__ import annotations

from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.config import load_config
from vaner.cli.commands.init import init_repo


def test_config_set_list_value_round_trips(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()
    result = runner.invoke(app, ["config", "set", "intent.skill_roots", '[".cursor/skills"]', "--path", str(temp_repo)])
    assert result.exit_code == 0
    config = load_config(temp_repo)
    assert config.intent.skill_roots == [".cursor/skills"]
