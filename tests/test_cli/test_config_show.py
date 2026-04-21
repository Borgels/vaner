from __future__ import annotations

from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.init import init_repo


def test_config_show_and_keys_include_intent_settings(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()

    show_result = runner.invoke(app, ["config", "show", "--path", str(temp_repo)])
    assert show_result.exit_code == 0
    assert "intent.lookback_turns" in show_result.stdout
    assert "intent.skills_loop.enabled" in show_result.stdout

    keys_result = runner.invoke(app, ["config", "keys", "--path", str(temp_repo)])
    assert keys_result.exit_code == 0
    assert "intent.lookback_turns" in keys_result.stdout
    assert "intent.skills_loop.enabled" in keys_result.stdout
    assert "intent.skills_loop.max_feedback_events_per_cycle" in keys_result.stdout
