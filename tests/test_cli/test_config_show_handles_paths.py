from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.init import init_repo


def test_config_show_json_serializes_path_fields(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()
    help_result = runner.invoke(app, ["config", "show", "--help"])
    if "--json" not in help_result.stdout:
        pytest.skip("config show --json unavailable on this CLI surface")
    result = runner.invoke(app, ["config", "show", "--path", str(temp_repo), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["repo_root"] == str(temp_repo.resolve())
