# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from vaner.cli.commands import mcp_clients


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_merge_adds_entry_to_empty_file(tmp_path) -> None:
    target = tmp_path / "mcp.json"
    result = mcp_clients._merge_json_server(  # type: ignore[attr-defined]
        client_id="cursor",
        path=target,
        container_key="mcpServers",
        launcher_cmd="/x/vaner",
        launcher_args=["mcp", "--path", "."],
        dry_run=False,
        force=False,
    )
    assert result.action == "added"
    parsed = _read_json(target)
    assert parsed["mcpServers"]["vaner"]["command"] == "/x/vaner"


def test_merge_preserves_unrelated_servers(tmp_path) -> None:
    target = tmp_path / "mcp.json"
    target.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}, indent=2) + "\n",
        encoding="utf-8",
    )
    result = mcp_clients._merge_json_server(  # type: ignore[attr-defined]
        client_id="cursor",
        path=target,
        container_key="mcpServers",
        launcher_cmd="/x/vaner",
        launcher_args=["mcp", "--path", "."],
        dry_run=False,
        force=False,
    )
    assert result.action in {"added", "updated"}
    parsed = _read_json(target)
    assert "other" in parsed["mcpServers"]
    assert "vaner" in parsed["mcpServers"]


def test_merge_updates_existing_vaner_entry_without_touching_others(tmp_path) -> None:
    target = tmp_path / "mcp.json"
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "x", "args": []},
                    "vaner": {"command": "old", "args": ["mcp"]},
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    result = mcp_clients._merge_json_server(  # type: ignore[attr-defined]
        client_id="cursor",
        path=target,
        container_key="mcpServers",
        launcher_cmd="/x/vaner",
        launcher_args=["mcp", "--path", "."],
        dry_run=False,
        force=False,
    )
    assert result.action == "updated"
    assert result.backup is not None and result.backup.exists()
    parsed = _read_json(target)
    assert parsed["mcpServers"]["other"]["command"] == "x"
    assert parsed["mcpServers"]["vaner"]["command"] == "/x/vaner"


def test_malformed_json_aborts_and_does_not_mutate(tmp_path) -> None:
    target = tmp_path / "mcp.json"
    broken = '{"mcpServers": {"broken": true'
    target.write_text(broken, encoding="utf-8")
    result = mcp_clients._merge_json_server(  # type: ignore[attr-defined]
        client_id="cursor",
        path=target,
        container_key="mcpServers",
        launcher_cmd="/x/vaner",
        launcher_args=["mcp", "--path", "."],
        dry_run=False,
        force=False,
    )
    assert result.action == "failed"
    assert target.read_text(encoding="utf-8") == broken
    assert list(tmp_path.glob("*.vaner-backup-*")) == []


def test_backup_created_before_mutation(tmp_path) -> None:
    target = tmp_path / "mcp.json"
    original = json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}, indent=2) + "\n"
    target.write_text(original, encoding="utf-8")
    result = mcp_clients._merge_json_server(  # type: ignore[attr-defined]
        client_id="cursor",
        path=target,
        container_key="mcpServers",
        launcher_cmd="/x/vaner",
        launcher_args=["mcp", "--path", "."],
        dry_run=False,
        force=False,
    )
    assert result.backup is not None
    assert result.backup.name.startswith("mcp.json.vaner-backup-")
    assert result.backup.read_text(encoding="utf-8") == original


def test_atomic_write_on_failure(tmp_path, monkeypatch) -> None:
    target = tmp_path / "mcp.json"
    original = json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}, indent=2) + "\n"
    target.write_text(original, encoding="utf-8")

    def _boom(_src, _dst):  # noqa: ANN001
        raise OSError("replace failed")

    monkeypatch.setattr(mcp_clients.os, "replace", _boom)
    result = mcp_clients._merge_json_server(  # type: ignore[attr-defined]
        client_id="cursor",
        path=target,
        container_key="mcpServers",
        launcher_cmd="/x/vaner",
        launcher_args=["mcp", "--path", "."],
        dry_run=False,
        force=False,
    )
    assert result.action == "failed"
    assert target.read_text(encoding="utf-8") == original


def test_yaml_continue_writer_shape(tmp_path) -> None:
    target = tmp_path / "vaner.yaml"
    result = mcp_clients._merge_yaml_continue(  # type: ignore[attr-defined]
        client_id="continue",
        path=target,
        launcher_cmd="/x/vaner",
        launcher_args=["mcp", "--path", "."],
        dry_run=False,
    )
    assert result.action == "added"
    assert (
        target.read_text(encoding="utf-8")
        == "name: vaner\nversion: 0.0.1\nschema: v1\ncommand: /x/vaner\nargs:\n  - mcp\n  - --path\n  - .\n"
    )


def test_json_context_servers_shape_for_zed(tmp_path) -> None:
    target = tmp_path / "settings.json"
    result = mcp_clients._merge_json_server(  # type: ignore[attr-defined]
        client_id="zed",
        path=target,
        container_key="context_servers",
        launcher_cmd="/x/vaner",
        launcher_args=["mcp", "--path", "."],
        dry_run=False,
        force=False,
    )
    assert result.action == "added"
    parsed = _read_json(target)
    assert parsed["context_servers"]["vaner"]["command"]["path"] == "/x/vaner"
