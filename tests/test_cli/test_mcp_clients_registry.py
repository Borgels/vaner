# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from vaner.cli.commands import mcp_clients


def test_all_ten_clients_registered() -> None:
    ids = {item.id for item in mcp_clients.CLIENTS}
    assert len(mcp_clients.CLIENTS) == 10
    assert ids == {
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


def test_every_client_has_unique_id_and_label() -> None:
    ids = [item.id for item in mcp_clients.CLIENTS]
    labels = [item.label for item in mcp_clients.CLIENTS]
    assert len(ids) == len(set(ids))
    assert len(labels) == len(set(labels))


def test_kinds_are_valid() -> None:
    allowed = {
        "json-mcpServers",
        "json-servers",
        "json-context_servers",
        "yaml-continue",
        "cli-claude",
        "cli-codex",
    }
    assert {item.kind for item in mcp_clients.CLIENTS}.issubset(allowed)


def test_resolve_launcher_prefers_absolute_path(monkeypatch) -> None:
    monkeypatch.setattr(mcp_clients.shutil, "which", lambda name: "/x/vaner" if name == "vaner" else None)
    command, args = mcp_clients.resolve_launcher(Path("/tmp/repo"))
    assert command == "/x/vaner"
    assert args == ["mcp", "--path", "/tmp/repo"]

    monkeypatch.setattr(mcp_clients.shutil, "which", lambda _name: None)
    fallback_cmd, fallback_args = mcp_clients.resolve_launcher()
    assert fallback_cmd == "vaner"
    assert fallback_args == ["mcp", "--path", "."]


def test_detect_all_is_pure_no_side_effects_on_empty_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    results = mcp_clients.detect_all(tmp_path / "repo")
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert before == after
    assert len(results) == 10
