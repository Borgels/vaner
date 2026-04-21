from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app


def test_mcp_stdio_command_wires_repo_root(temp_repo: Path, monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    async def _fake_stdio(repo_root: Path) -> None:
        captured["repo_root"] = repo_root

    async def _fake_sse(repo_root: Path, host: str, port: int) -> None:
        captured["repo_root"] = repo_root
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("vaner.mcp.server.run_stdio", _fake_stdio)
    monkeypatch.setattr("vaner.mcp.server.run_sse", _fake_sse)

    result = runner.invoke(app, ["mcp", "--path", str(temp_repo), "--transport", "stdio"])
    assert result.exit_code == 0
    assert captured["repo_root"] == temp_repo


def test_mcp_sse_non_loopback_host_rejected(temp_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "--path", str(temp_repo), "--transport", "sse", "--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "MCP SSE transport only supports loopback hosts by default." in result.stderr


def test_mcp_sse_command_wires_host_and_port(temp_repo: Path, monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    async def _fake_stdio(repo_root: Path) -> None:
        captured["repo_root"] = repo_root

    async def _fake_sse(repo_root: Path, host: str, port: int) -> None:
        captured["repo_root"] = repo_root
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("vaner.mcp.server.run_stdio", _fake_stdio)
    monkeypatch.setattr("vaner.mcp.server.run_sse", _fake_sse)

    result = runner.invoke(
        app,
        ["mcp", "--path", str(temp_repo), "--transport", "sse", "--host", "127.0.0.1", "--port", "8572"],
    )
    assert result.exit_code == 0
    assert captured["repo_root"] == temp_repo
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8572


def test_mcp_smoke_mode_runs_probe(temp_repo: Path, monkeypatch) -> None:
    runner = CliRunner()
    help_result = runner.invoke(app, ["mcp", "--help"])
    if "--smoke" not in help_result.stdout:
        pytest.skip("mcp --smoke unavailable on this CLI surface")

    async def _fake_stdio(repo_root: Path) -> None:
        raise AssertionError(f"stdio server should not start in smoke mode: {repo_root}")

    async def _fake_sse(repo_root: Path, host: str, port: int) -> None:
        raise AssertionError(f"sse server should not start in smoke mode: {repo_root} {host} {port}")

    async def _fake_smoke(repo_root: Path) -> dict[str, object]:
        return {"ok": True, "repo_root": str(repo_root)}

    monkeypatch.setattr("vaner.mcp.server.run_stdio", _fake_stdio)
    monkeypatch.setattr("vaner.mcp.server.run_sse", _fake_sse)
    monkeypatch.setattr("vaner.mcp.server.run_smoke_probe", _fake_smoke)

    result = runner.invoke(app, ["mcp", "--path", str(temp_repo), "--smoke"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout.lower()
