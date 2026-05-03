from __future__ import annotations

import json

from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.init import init_repo


def test_focus_route_status_json_works_with_local_fallback(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()

    result = runner.invoke(app, ["focus", "route", "status", "--repo-root", str(temp_repo), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "effective_route" in payload
    assert "workspace_options" in payload


def test_focus_route_set_json_writes_local_fallback(temp_repo) -> None:
    init_repo(temp_repo)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "focus",
            "route",
            "set",
            "--repo-root",
            str(temp_repo),
            "--workspace-policy",
            "pinned",
            "--workspace",
            str(temp_repo),
            "--client",
            "cursor",
            "--resource-mode",
            "performance",
            "--device",
            "cpu",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["effective_route"]["workspace_policy"] == "pinned"
    assert payload["effective_route"]["resource_mode"] == "performance"
    assert payload["effective_route"]["device"] == "cpu"
