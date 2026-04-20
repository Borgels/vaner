from __future__ import annotations

from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.config import load_config
from vaner.cli.commands.init import init_repo


def test_config_set_supports_backend_and_nested_gateway_keys(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()
    result_model = runner.invoke(app, ["config", "set", "backend.model", "qwen3.5:35b", "--path", str(temp_repo)])
    result_route = runner.invoke(
        app,
        ["config", "set", "gateway.routes.default", "https://api.openai.com/v1", "--path", str(temp_repo)],
    )
    result_skills = runner.invoke(app, ["config", "set", "intent.skills_loop.enabled", "false", "--path", str(temp_repo)])
    assert result_model.exit_code == 0
    assert result_route.exit_code == 0
    assert result_skills.exit_code == 0
    config = load_config(temp_repo)
    assert config.backend.model == "qwen3.5:35b"
    assert config.gateway.routes["default"] == "https://api.openai.com/v1"
    assert config.intent.skills_loop_enabled is False
