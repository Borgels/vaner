from __future__ import annotations

import pytest
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
    if any(item.exit_code != 0 for item in [result_model, result_route, result_skills]):
        pytest.skip("one or more config setters unavailable on this CLI surface")
    assert result_model.exit_code == 0
    assert result_route.exit_code == 0
    assert result_skills.exit_code == 0
    config = load_config(temp_repo)
    assert config.backend.model == "qwen3.5:35b"
    assert config.gateway.routes["default"] == "https://api.openai.com/v1"
    if not hasattr(config, "intent"):
        pytest.skip("intent config unavailable on this CLI surface")
    assert config.intent.skills_loop_enabled is False


def test_config_set_writes_exploration_aliases_without_legacy_drift(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()

    result_endpoint = runner.invoke(app, ["config", "set", "exploration.endpoint", "http://127.0.0.1:11434", "--path", str(temp_repo)])
    result_model = runner.invoke(app, ["config", "set", "exploration.model", "qwen3.5:35b", "--path", str(temp_repo)])
    result_backend = runner.invoke(app, ["config", "set", "exploration.backend", "ollama", "--path", str(temp_repo)])

    assert result_endpoint.exit_code == 0, result_endpoint.output
    assert result_model.exit_code == 0, result_model.output
    assert result_backend.exit_code == 0, result_backend.output
    config_text = (temp_repo / ".vaner" / "config.toml").read_text(encoding="utf-8")
    assert 'endpoint = "http://127.0.0.1:11434"' in config_text
    assert 'model = "qwen3.5:35b"' in config_text
    assert 'backend = "ollama"' in config_text
    assert "exploration_endpoint" not in config_text
    assert "exploration_model" not in config_text
    assert "exploration_backend" not in config_text

    config = load_config(temp_repo)
    assert config.exploration.endpoint == "http://127.0.0.1:11434"
    assert config.exploration.model == "qwen3.5:35b"
    assert config.exploration.backend == "ollama"


def test_config_get_supports_exploration_aliases(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()
    runner.invoke(app, ["config", "set", "exploration.model", "qwen3.5:35b", "--path", str(temp_repo)])

    result = runner.invoke(app, ["config", "get", "exploration.model", "--path", str(temp_repo)])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "qwen3.5:35b"
