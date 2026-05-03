# SPDX-License-Identifier: Apache-2.0

"""Tests for ``vaner launch <client>`` and the underlying
:mod:`vaner.cli.commands.launch` orchestrator.

The orchestrator runs every applicable leverage layer in one pass:

    Layer 1 — MCP server entry
    Layer 2 — Primer (rules file)
    Layer 3 — Skill / workflow / prompt
    Layer 4 — Plugin / hook (where Vaner ships one)

Each layer reports its own status. A failure at one layer doesn't
stop the rest. Layers Vaner can't ship into for a given client are
reported as ``applicable=False`` so the desktop wizard / CLI render
``—`` instead of ✓ / ✗.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.launch import LaunchResult, launch_client

runner = CliRunner()


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin HOME / Path.home() / shutil.which / APPDATA so user-scope
    surfaces (Claude Code skill, Codex CLI skill) write under tmp_path
    with no leakage to the real machine.
    """

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setattr(
        "vaner.cli.commands.mcp_clients.shutil.which",
        lambda name: f"/fake/bin/{name}" if name == "vaner" else None,
    )
    return home


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def test_launch_cursor_runs_full_leverage_stack(fake_home: Path, tmp_path: Path) -> None:
    """For Cursor, all four layers are applicable and Vaner ships
    a writer for each — MCP + primer + skill + plugin/hook (where
    plugin layer for Cursor today is "not applicable from launch"
    since the Cursor plugin installs via marketplace, but the rest
    write here)."""

    (fake_home / ".cursor").mkdir()  # detect cursor
    repo = tmp_path / "repo"
    repo.mkdir()
    result = launch_client("cursor", repo)

    assert isinstance(result, LaunchResult)
    assert result.client_id == "cursor"
    assert result.detected is True

    by_name = {layer.layer: layer for layer in result.layers}
    assert {"mcp", "primer", "skill", "hook"} == set(by_name.keys())

    # Vaner-side writes: MCP, primer, skill all applicable.
    assert by_name["mcp"].applicable is True
    assert by_name["mcp"].action in ("added", "updated")
    assert by_name["primer"].applicable is True
    assert by_name["primer"].action in ("added", "updated")
    assert by_name["skill"].applicable is True
    assert by_name["skill"].action in ("added", "updated")

    # Cursor's plugin/hooks live in the cursor-plugins/vaner bundle —
    # not written by ``vaner launch``. Layer reports applicable=False
    # so the CLI shows ``—``.
    assert by_name["hook"].applicable is False

    assert result.overall == "ready"


def test_launch_zed_only_writes_mcp_and_primer(fake_home: Path, tmp_path: Path) -> None:
    """Zed has no skill abstraction and no third-party hook API, so
    only MCP + primer are applicable. Both write; overall is ready."""

    (fake_home / ".config" / "zed").mkdir(parents=True)  # detect zed
    repo = tmp_path / "repo"
    repo.mkdir()
    result = launch_client("zed", repo)

    by_name = {layer.layer: layer for layer in result.layers}
    assert by_name["mcp"].applicable is True
    assert by_name["primer"].applicable is True
    assert by_name["skill"].applicable is False
    assert by_name["hook"].applicable is False
    assert result.overall == "ready"


def test_launch_cline_writes_all_four_layers(fake_home: Path, tmp_path: Path) -> None:
    """Cline has every layer Vaner ships into via launch — MCP,
    primer (.clinerules), workflow (.clinerules/workflows/), and
    UserPromptSubmit hook (.clinerules/hooks/)."""

    # Cline lives under the VS Code user dir; create it so detection succeeds.
    from vaner.cli.commands import mcp_clients

    mcp_clients._vscode_user_dir().mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    result = launch_client("cline", repo)

    by_name = {layer.layer: layer for layer in result.layers}
    for layer_name in ("mcp", "primer", "skill", "hook"):
        assert by_name[layer_name].applicable is True, f"{layer_name} should be applicable for cline"
        assert by_name[layer_name].action in ("added", "updated"), (
            f"{layer_name} should write on a fresh tree; got {by_name[layer_name].action}"
        )
    assert result.overall == "ready"


def test_launch_claude_code_wires_mcp_skill_and_plugin(
    fake_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "vaner.cli.commands.mcp_clients.shutil.which",
        lambda name: f"/fake/bin/{name}" if name in {"vaner", "claude"} else None,
    )

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["/fake/bin/claude", "mcp", "list"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("vaner.cli.commands.mcp_clients.subprocess.run", fake_run)
    repo = tmp_path / "repo"
    repo.mkdir()

    result = launch_client("claude-code", repo)
    by_name = {layer.layer: layer for layer in result.layers}

    assert by_name["mcp"].action == "added"
    assert by_name["skill"].path == fake_home / ".claude" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md"
    assert by_name["hook"].path == fake_home / ".claude" / "plugins" / "vaner"
    assert (fake_home / ".claude" / "plugins" / "vaner" / ".claude-plugin" / "plugin.json").exists()
    assert any(call[:3] == ["/fake/bin/claude", "mcp", "add"] for call in calls)


def test_launch_codex_cli_uses_detected_binary_for_mcp(
    fake_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "vaner.cli.commands.mcp_clients.shutil.which",
        lambda name: f"/fake/bin/{name}" if name in {"vaner", "codex"} else None,
    )

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["/fake/bin/codex", "mcp", "list"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("vaner.cli.commands.mcp_clients.subprocess.run", fake_run)
    repo = tmp_path / "repo"
    repo.mkdir()

    result = launch_client("codex-cli", repo)
    by_name = {layer.layer: layer for layer in result.layers}

    assert by_name["mcp"].action == "added"
    assert by_name["skill"].path == fake_home / ".codex" / "skills" / "vaner-feedback" / "SKILL.md"
    assert by_name["hook"].path == fake_home / ".codex" / "plugins" / "vaner-codex"
    assert (fake_home / ".codex" / "plugins" / "vaner-codex" / ".codex-plugin" / "plugin.json").exists()
    config = tomllib.loads((fake_home / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert config["plugins"]["vaner-codex@vaner-local"]["enabled"] is True
    assert config["features"]["codex_hooks"] is True
    hooks = json.loads((fake_home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    prompt_command = hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert str(fake_home / ".codex" / "plugins" / "vaner-codex" / "scripts" / "codex_prompt_submit.py") in prompt_command
    version = json.loads((fake_home / ".codex" / "plugins" / "vaner-codex" / ".codex-plugin" / "plugin.json").read_text())[
        "version"
    ]
    assert (
        fake_home
        / ".codex"
        / "plugins"
        / "cache"
        / "vaner-local"
        / "vaner-codex"
        / version
        / ".codex-plugin"
        / "plugin.json"
    ).exists()
    assert any(call[:3] == ["/fake/bin/codex", "mcp", "add"] for call in calls)

    from vaner.cli.commands import mcp_clients

    verified = {row.client_id: row for row in mcp_clients.verify_all(repo)}
    assert verified["codex-cli"].layers["plugin"].wired is True


def test_launch_codex_cli_dry_run_does_not_add_mcp(
    fake_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "vaner.cli.commands.mcp_clients.shutil.which",
        lambda name: f"/fake/bin/{name}" if name in {"vaner", "codex"} else None,
    )

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["/fake/bin/codex", "mcp", "list"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"dry-run should not execute {args}")

    monkeypatch.setattr("vaner.cli.commands.mcp_clients.subprocess.run", fake_run)
    repo = tmp_path / "repo"
    repo.mkdir()

    result = launch_client("codex-cli", repo, dry_run=True)
    by_name = {layer.layer: layer for layer in result.layers}

    assert by_name["mcp"].action == "added"
    assert all(call[:3] == ["/fake/bin/codex", "mcp", "list"] for call in calls)


def test_launch_unknown_client_returns_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = launch_client("nope-some-client", repo)
    assert result.overall == "missing"
    assert result.detected is False


def test_launch_dry_run_writes_nothing(fake_home: Path, tmp_path: Path) -> None:
    (fake_home / ".cursor").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    result = launch_client("cursor", repo, dry_run=True)
    # Status reports ``added`` per layer (would-have-written), but the
    # actual files don't appear on disk.
    assert result.overall == "ready"
    assert not (repo / ".cursor" / "rules" / "vaner.mdc").exists()
    assert not (repo / ".cursor" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md").exists()


def test_launch_skip_layers_drops_named_layers(fake_home: Path, tmp_path: Path) -> None:
    """``skip_layers=("primer",)`` opts out of primer writing but
    still runs every other applicable layer. The skipped layer
    reports as ``applicable=True, action="skipped"`` (so callers can
    distinguish "user opted out" from "this client doesn't support
    this layer" — ``not-applicable`` is reserved for the latter).
    """

    (fake_home / ".cursor").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    result = launch_client("cursor", repo, skip_layers=("primer",))
    by_name = {layer.layer: layer for layer in result.layers}
    assert by_name["primer"].applicable is True
    assert by_name["primer"].action == "skipped"
    # MCP and skill still wrote.
    assert by_name["mcp"].action in ("added", "updated")
    assert by_name["skill"].action in ("added", "updated")
    assert not (repo / ".cursor" / "rules" / "vaner.mdc").exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_launch_cli_emits_per_layer_status(fake_home: Path, tmp_path: Path) -> None:
    (fake_home / ".cursor").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["launch", "cursor", "--repo-root", str(repo), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["client_id"] == "cursor"
    assert payload["overall"] == "ready"
    layer_names = {layer["layer"] for layer in payload["layers"]}
    assert {"mcp", "primer", "skill", "hook"} == layer_names


def test_launch_cli_pretty_format_renders_per_layer_lines(fake_home: Path, tmp_path: Path) -> None:
    (fake_home / ".cursor").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["launch", "cursor", "--repo-root", str(repo)],
    )
    assert result.exit_code == 0, result.output
    # Pretty output names each layer.
    for layer_name in ("mcp", "primer", "skill", "hook"):
        assert layer_name in result.output


def test_launch_cli_unknown_client_exits_nonzero(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        app,
        ["launch", "nonsense-client", "--repo-root", str(repo), "--format", "json"],
    )
    # Exit non-zero so CI scripts can detect "Vaner couldn't wire that client".
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["overall"] == "missing"
