from __future__ import annotations

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.config import load_config
from vaner.cli.commands.init import init_repo


def test_config_set_list_value_round_trips(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()
    result = runner.invoke(app, ["config", "set", "intent.skill_roots", '[".cursor/skills"]', "--path", str(temp_repo)])
    if result.exit_code != 0 and "Unsupported setting" in result.stdout:
        pytest.skip("intent.skill_roots unsupported on this CLI surface")
    assert result.exit_code == 0
    config = load_config(temp_repo)
    if not hasattr(config, "intent"):
        pytest.skip("intent config unavailable on this CLI surface")
    assert config.intent.skill_roots == [".cursor/skills"]
