from __future__ import annotations

from typer.testing import CliRunner

from vaner.cli.commands.app import app


def test_help_includes_expected_panels() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for panel in (
        "Get started",
        "Use with an agent",
        "Inspect and debug",
        "Background and local",
        "Configure",
        "Benchmark",
    ):
        assert panel in result.stdout
