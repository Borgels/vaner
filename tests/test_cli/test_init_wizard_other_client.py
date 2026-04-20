# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typer.testing import CliRunner

from vaner.cli.commands.app import app

runner = CliRunner()


def test_other_prints_generic_snippet_and_docs(temp_repo) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(temp_repo),
            "--no-interactive",
            "--backend-preset",
            "skip",
            "--clients",
            "other",
        ],
    )
    assert result.exit_code == 0
    assert "docs.vaner.ai/mcp" in result.stdout
    assert "issues/new?labels=client-support" in result.stdout
    assert '"mcpServers"' in result.stdout


def test_footer_always_present(temp_repo) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(temp_repo),
            "--no-interactive",
            "--backend-preset",
            "skip",
            "--clients",
            "none",
        ],
    )
    assert result.exit_code == 0
    assert "Using a different MCP client?" in result.stdout
