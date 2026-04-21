from __future__ import annotations

import json

from vaner.cli.commands import mcp_clients


def test_merge_json_server_is_idempotent_and_caps_backups(tmp_path, monkeypatch) -> None:
    target = tmp_path / "mcp.json"
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}, indent=2) + "\n", encoding="utf-8")

    counter = {"value": 1710000000}

    def _fake_time() -> float:
        counter["value"] += 1
        return float(counter["value"])

    monkeypatch.setattr(mcp_clients.time, "time", _fake_time)

    created_backups = 0
    for _ in range(10):
        result = mcp_clients._merge_json_server(  # type: ignore[attr-defined]
            client_id="cursor",
            path=target,
            container_key="mcpServers",
            launcher_cmd="/x/vaner",
            launcher_args=["mcp", "--path", "."],
            dry_run=False,
            force=False,
        )
        if result.backup is not None:
            created_backups += 1

    backups = sorted(tmp_path.glob("mcp.json.vaner-backup-*"))
    assert created_backups == 1
    assert len(backups) <= 3
