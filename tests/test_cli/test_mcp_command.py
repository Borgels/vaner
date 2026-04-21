from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from vaner.cli.commands.app import app


def test_mcp_stdio_command_wires_repo_root(temp_repo: Path, monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    async def _fake_stdio(repo_root: Path, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured.update(kwargs)

    async def _fake_sse(repo_root: Path, host: str, port: int, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured["host"] = host
        captured["port"] = port
        captured.update(kwargs)

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

    async def _fake_stdio(repo_root: Path, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured.update(kwargs)

    async def _fake_sse(repo_root: Path, host: str, port: int, **kwargs: object) -> None:
        captured["repo_root"] = repo_root
        captured["host"] = host
        captured["port"] = port
        captured.update(kwargs)

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
