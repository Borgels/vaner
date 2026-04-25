# SPDX-License-Identifier: Apache-2.0

"""Tests for `vaner clients` CLI (0.8.5 WS12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.clients import clients_app

runner = CliRunner()


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin HOME / Path.home() / shutil.which / APPDATA so clients are
    detectable under tmp_path with no leakage to the real machine.

    Mirrors the pattern from `tests/test_cli/test_init.py`.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    # Make `vaner` look installed at a stable absolute path so the launcher
    # ends up deterministic across platforms.
    monkeypatch.setattr(
        "vaner.cli.commands.mcp_clients.shutil.which",
        lambda name: f"/fake/bin/{name}" if name == "vaner" else None,
    )
    return home


def _seed_cursor(home: Path, repo_root: Path) -> None:
    # Cursor's user dir is what `_detect_cursor` looks for; create it.
    (home / ".cursor").mkdir(parents=True, exist_ok=True)


def _seed_claude_desktop(home: Path) -> None:
    # Linux path on the fake home.
    (home / ".config" / "Claude").mkdir(parents=True, exist_ok=True)


def _seed_continue(home: Path) -> None:
    (home / ".continue").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# detect
# ---------------------------------------------------------------------------


def test_detect_returns_every_known_client(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        clients_app,
        ["detect", "--repo-root", str(repo), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    ids = {c["id"] for c in payload["clients"]}
    expected = {
        "cursor",
        "claude-desktop",
        "claude-code",
        "vscode-copilot",
        "codex-cli",
        "windsurf",
        "zed",
        "continue",
        "cline",
        "roo",
    }
    assert expected.issubset(ids)


def test_detect_marks_seeded_clients_as_installed(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    _seed_claude_desktop(fake_home)
    _seed_continue(fake_home)
    result = runner.invoke(clients_app, ["detect", "--repo-root", str(repo), "--format", "json"])
    payload = json.loads(result.output)
    by_id = {c["id"]: c for c in payload["clients"]}
    assert by_id["cursor"]["detected"] is True
    assert by_id["claude-desktop"]["detected"] is True
    assert by_id["continue"]["detected"] is True
    # Unseeded clients remain missing.
    assert by_id["zed"]["detected"] is False
    assert by_id["windsurf"]["detected"] is False


def test_detect_pretty_format_renders_table(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    result = runner.invoke(clients_app, ["detect", "--repo-root", str(repo)])
    assert result.exit_code == 0
    assert "MCP clients" in result.output
    assert "Cursor" in result.output


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def test_install_writes_to_cursor(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    result = runner.invoke(
        clients_app,
        ["install", "cursor", "--repo-root", str(repo), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["results"][0]["client_id"] == "cursor"
    assert payload["results"][0]["action"] in ("added", "updated")
    config_path = Path(payload["results"][0]["path"])
    assert config_path.exists()
    blob = json.loads(config_path.read_text(encoding="utf-8"))
    assert "vaner" in blob["mcpServers"]
    assert blob["mcpServers"]["vaner"]["command"] == "/fake/bin/vaner"


def test_install_all_writes_to_every_detected(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    _seed_claude_desktop(fake_home)
    result = runner.invoke(
        clients_app,
        ["install", "--all", "--repo-root", str(repo), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    ids = [r["client_id"] for r in payload["results"]]
    assert "cursor" in ids
    assert "claude-desktop" in ids


def test_install_idempotent_when_entry_present(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    runner.invoke(clients_app, ["install", "cursor", "--repo-root", str(repo)])
    second = runner.invoke(
        clients_app,
        ["install", "cursor", "--repo-root", str(repo), "--format", "json"],
    )
    payload = json.loads(second.output)
    assert payload["results"][0]["action"] == "skipped"


def test_install_preserves_user_other_servers(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    cursor_cfg = fake_home / ".cursor" / "mcp.json"
    cursor_cfg.write_text(
        json.dumps({"mcpServers": {"github": {"command": "/usr/local/bin/gh-mcp", "args": []}}}, indent=2),
        encoding="utf-8",
    )
    result = runner.invoke(clients_app, ["install", "cursor", "--repo-root", str(repo)])
    assert result.exit_code == 0
    blob = json.loads(cursor_cfg.read_text(encoding="utf-8"))
    assert "github" in blob["mcpServers"], "user's other MCP servers must survive install"
    assert "vaner" in blob["mcpServers"]


def test_install_dry_run_does_not_write(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    config_path = fake_home / ".cursor" / "mcp.json"
    assert not config_path.exists()
    result = runner.invoke(
        clients_app,
        ["install", "cursor", "--repo-root", str(repo), "--dry-run"],
    )
    assert result.exit_code == 0
    # The config file must NOT exist after a dry-run.
    assert not config_path.exists(), "dry-run must not write any files"


def test_install_unknown_client_errors(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(clients_app, ["install", "nonsense", "--repo-root", str(repo)])
    assert result.exit_code != 0
    assert "unknown client" in result.output.lower() or "Known:" in result.output


def test_install_requires_name_or_all(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(clients_app, ["install", "--repo-root", str(repo)])
    assert result.exit_code != 0


def test_install_claude_desktop_uses_repo_scoped_server_key(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _seed_claude_desktop(fake_home)
    result = runner.invoke(
        clients_app,
        ["install", "claude-desktop", "--repo-root", str(repo)],
    )
    assert result.exit_code == 0
    cfg = fake_home / ".config" / "Claude" / "claude_desktop_config.json"
    blob = json.loads(cfg.read_text(encoding="utf-8"))
    # Wizard convention: vaner-<reponame> so multiple repos coexist.
    assert "vaner-myrepo" in blob["mcpServers"]


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def test_uninstall_removes_only_vaner_entries(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    cursor_cfg = fake_home / ".cursor" / "mcp.json"
    cursor_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "/usr/local/bin/gh-mcp", "args": []},
                    "vaner": {"command": "/fake/bin/vaner", "args": ["mcp", "--path", "."]},
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        clients_app,
        ["uninstall", "cursor", "--repo-root", str(repo)],
    )
    assert result.exit_code == 0
    blob = json.loads(cursor_cfg.read_text(encoding="utf-8"))
    assert "vaner" not in blob["mcpServers"]
    assert "github" in blob["mcpServers"]


def test_uninstall_when_no_entry_skips_cleanly(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    # No vaner entry; just an empty config.
    cfg = fake_home / ".cursor" / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {}}, indent=2), encoding="utf-8")
    result = runner.invoke(
        clients_app,
        ["uninstall", "cursor", "--repo-root", str(repo), "--format", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["results"][0]["action"] == "skipped"


# ---------------------------------------------------------------------------
# doctor — launcher drift
# ---------------------------------------------------------------------------


def test_doctor_clean_when_no_drift(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    runner.invoke(clients_app, ["install", "cursor", "--repo-root", str(repo)])
    result = runner.invoke(
        clients_app,
        ["doctor", "--repo-root", str(repo), "--format", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["drift_count"] == 0


def test_doctor_detects_launcher_drift(fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_cursor(fake_home, repo)
    runner.invoke(clients_app, ["install", "cursor", "--repo-root", str(repo)])
    # Now simulate `vaner` moving (e.g. uv → pipx reinstall).
    monkeypatch.setattr(
        "vaner.cli.commands.mcp_clients.shutil.which",
        lambda name: "/new/bin/vaner" if name == "vaner" else None,
    )
    result = runner.invoke(
        clients_app,
        ["doctor", "--repo-root", str(repo), "--format", "json"],
    )
    assert result.exit_code != 0  # non-zero exit signals drift to CI
    payload = json.loads(result.output)
    assert payload["drift_count"] >= 1
    cursor_drift = next(d for d in payload["drift"] if d["client_id"] == "cursor")
    assert cursor_drift["drift"] is True
    assert cursor_drift["current_in_config"] == "/fake/bin/vaner"
    assert cursor_drift["expected"] == "/new/bin/vaner"


# ---------------------------------------------------------------------------
# Top-level command registration
# ---------------------------------------------------------------------------


def test_clients_subapp_registered_on_top_level_app() -> None:
    result = runner.invoke(app, ["clients", "--help"])
    assert result.exit_code == 0
    assert "MCP clients" in result.output or "mcp clients" in result.output.lower()
