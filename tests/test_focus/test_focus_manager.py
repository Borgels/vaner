from __future__ import annotations

from pathlib import Path

import pytest

from vaner.cli.commands import mcp_clients
from vaner.focus import FocusManager, _is_local_url, _process_info_matches
from vaner.models.config import VanerConfig


def _config(tmp_path: Path) -> VanerConfig:
    repo = tmp_path / "repo"
    (repo / ".vaner").mkdir(parents=True)
    (repo / ".vaner" / "config.toml").write_text("", encoding="utf-8")
    return VanerConfig(repo_root=repo, store_path=repo / ".vaner" / "store.db", telemetry_path=repo / ".vaner" / "telemetry.db")


@pytest.fixture(autouse=True)
def _stable_client_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = mcp_clients.ClientSpec(
        "cursor",
        "Cursor",
        "json-mcpServers",
        lambda repo: repo / ".cursor",
        lambda repo: repo / ".cursor" / "mcp.json",
        "Cursor user MCP config",
    )
    monkeypatch.setattr(
        "vaner.focus.mcp_clients.detect_all",
        lambda repo: [
            mcp_clients.DetectedClient(
                spec=cursor,
                status=mcp_clients.ClientStatus.CONFIGURED,
                path=repo / ".cursor" / "mcp.json",
                detail="already configured",
            )
        ],
    )
    monkeypatch.setattr("vaner.focus._client_process_running", lambda _client_id: False)


def test_focus_defaults_to_idle_without_running_client(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))

    state = manager.build_state()

    assert state.status in {"idle", "standby"}
    assert state.proactive_allowed is False
    assert state.active_workspace_id is None
    assert state.explanation
    assert state.why_not


def test_work_here_selects_one_active_workspace(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))

    state = manager.work_here(manager.repo_root)

    assert state.status == "active"
    assert state.proactive_allowed is True
    assert state.active_workspace_id == manager.current_workspace_id
    assert [ws for ws in state.workspaces if ws.tier == "active"]


def test_pause_blocks_repo_reads_and_bumps_epoch(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))
    active = manager.work_here(manager.repo_root)

    paused = manager.pause(manager.repo_root)
    gate = manager.proactive_gate(job_class="prepared_work")

    assert paused.focus_epoch > active.focus_epoch
    assert paused.status != "active"
    assert gate.status == "deferred"
    assert gate.defer_reason in {"workspace_paused", "no_supported_client_running", "focus_not_active"}


def test_manual_only_blocks_proactive_work(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))
    manager.work_here(manager.repo_root)

    state = manager.set_mode("manual-only")
    gate = manager.proactive_gate(job_class="prepared_work")

    assert state.status == "manual_only"
    assert state.proactive_allowed is False
    assert gate.status == "deferred"
    assert gate.defer_reason == "manual_only"


def test_external_observation_activates_wired_workspace(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))

    state = manager.record_observation(
        {
            "client_id": "cursor",
            "display_name": "Cursor",
            "running": True,
            "foreground": True,
            "integration_state": "wired",
            "workspace_path": str(manager.repo_root),
            "focus_confidence": 0.95,
        }
    )

    assert state.status == "active"
    assert state.active_workspace_id == manager.current_workspace_id
    assert state.proactive_allowed is True


def test_unknown_observation_client_is_ignored(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))

    state = manager.record_observation(
        {
            "client_id": "random-app",
            "display_name": "Random App",
            "running": True,
            "foreground": True,
            "integration_state": "wired",
            "workspace_path": str(manager.repo_root),
            "focus_confidence": 1.0,
        }
    )

    assert state.active_workspace_id is None
    assert state.proactive_allowed is False


def test_running_cli_managed_client_activates_current_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FocusManager(_config(tmp_path))
    spec = mcp_clients.ClientSpec(
        "codex-cli",
        "Codex CLI",
        "cli-codex",
        lambda _repo: Path("/usr/bin/codex"),
        lambda _repo: None,
        "codex mcp add command",
    )
    monkeypatch.setattr(
        "vaner.focus.mcp_clients.detect_all",
        lambda _repo: [
            mcp_clients.DetectedClient(
                spec=spec,
                status=mcp_clients.ClientStatus.CONFIGURED,
                path=None,
                detail="already configured",
            )
        ],
    )
    monkeypatch.setattr("vaner.focus._client_process_running", lambda client_id: client_id == "codex-cli")

    state = manager.build_state()

    assert state.status == "active"
    assert state.active_client_id == "codex-cli"
    assert state.active_workspace_id == manager.current_workspace_id
    assert state.proactive_allowed is True
    assert state.detected_clients[0].workspace_hints == [str(manager.repo_root)]


def test_corrupt_focus_preferences_recover_to_defaults(tmp_path: Path) -> None:
    config = _config(tmp_path)
    focus_path = config.repo_root / ".vaner" / "focus.json"
    focus_path.write_text(
        '{"focus_epoch": "not-an-int", "mode": "bad", "paused_workspace_paths": "not-a-list"}',
        encoding="utf-8",
    )

    state = FocusManager(config).build_state()

    assert state.mode == "auto"
    assert state.focus_epoch == 0
    assert all(ws.paused is False for ws in state.workspaces)


def test_invalid_modes_are_rejected_before_persisting(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))

    with pytest.raises(ValueError, match="mode must be"):
        manager.set_mode("surprise")
    with pytest.raises(ValueError, match="resource_mode must be"):
        manager.set_resource_mode("cloud-burst")

    state = manager.build_state()
    assert state.mode == "auto"
    assert state.resource_mode == "balanced"


def test_build_state_does_not_write_focus_file_without_preferences(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))
    focus_path = manager.repo_root / ".vaner" / "focus.json"

    manager.build_state()

    assert not focus_path.exists()


def test_route_state_summarizes_active_workspace_client_and_compute(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))
    manager.record_observation(
        {
            "client_id": "cursor",
            "display_name": "Cursor",
            "running": True,
            "foreground": True,
            "integration_state": "wired",
            "workspace_path": str(manager.repo_root),
            "focus_confidence": 0.95,
        }
    )

    route = manager.route_state()

    assert route.effective_route["workspace"]["display_name"] == manager.repo_root.name
    assert route.effective_route["client"]["display_name"] == "Cursor"
    assert route.effective_route["workspace_policy"] == "auto"
    assert route.effective_route["resource_mode"] == "balanced"
    assert route.client_options[0]["id"] == "cursor"
    assert route.explanation == f"Vaner is following Cursor in {manager.repo_root.name}."


def test_route_preferences_can_pin_workspace_and_prefer_client(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))

    state = manager.set_route_preferences(
        workspace_policy="pinned",
        workspace_path=manager.repo_root,
        client_id="cursor",
        resource_mode="performance",
    )
    route = manager.route_state()

    assert state.focus_epoch == 1
    assert route.effective_route["workspace_policy"] == "pinned"
    assert route.effective_route["resource_mode"] == "performance"
    assert route.effective_route["client"]["id"] == "cursor"
    assert route.effective_route["client"]["preferred"] is True


def test_route_preferences_auto_clears_overrides(tmp_path: Path) -> None:
    manager = FocusManager(_config(tmp_path))
    manager.set_route_preferences(workspace_policy="pinned", workspace_path=manager.repo_root, client_id="cursor")

    manager.set_route_preferences(workspace_policy="auto", client_id=None)
    route = manager.route_state()

    assert route.effective_route["workspace_policy"] == "auto"
    assert route.effective_route["client"] is None


def test_local_url_detection_does_not_match_substrings() -> None:
    assert _is_local_url("http://127.0.0.1:11434/v1") is True
    assert _is_local_url("http://localhost:1234") is True
    assert _is_local_url("http://notlocalhost.example/v1") is False
    assert _is_local_url("https://api.example.com/v1") is False


def test_process_matching_does_not_confuse_codex_with_vs_code() -> None:
    codex_cmdline = [
        "node",
        "/home/abo/.npm-global/bin/codex",
    ]

    assert _process_info_matches("codex-cli", "node", codex_cmdline) is True
    assert _process_info_matches("vscode-copilot", "node", codex_cmdline) is False
    assert _process_info_matches("vscode-copilot", "codex", ["/usr/bin/codex"]) is False


def test_process_matching_ignores_transient_claude_management_commands() -> None:
    assert _process_info_matches("claude-code", "claude", ["claude", "mcp", "list"]) is False
    assert _process_info_matches("claude-desktop", "claude", ["claude", "mcp", "list"]) is False
    assert _process_info_matches("claude-code", "claude", ["claude"]) is True
    assert _process_info_matches("claude-desktop", "claude-desktop", ["claude-desktop"]) is True
