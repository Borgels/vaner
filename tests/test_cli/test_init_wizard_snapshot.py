# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from vaner.cli.commands import init as init_module
from vaner.cli.commands.mcp_clients import ClientSpec, ClientStatus, DetectedClient


def _detected(client_id: str, status: ClientStatus = ClientStatus.INSTALLED) -> DetectedClient:
    spec = ClientSpec(
        id=client_id,
        label=client_id,
        kind="json-mcpServers",
        detect=lambda _repo_root: Path("/tmp"),
        config_path=lambda _repo_root: Path(f"/tmp/{client_id}.json"),
        manual_snippet_hint=f"{client_id} hint",
    )
    return DetectedClient(spec=spec, status=status, path=Path(f"/tmp/{client_id}.json"))


def test_picker_snapshot(monkeypatch) -> None:
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, width=120)
    detected = [_detected("cursor"), _detected("claude-code"), _detected("codex-cli", ClientStatus.MISSING)]
    monkeypatch.setattr(init_module, "_prompt", lambda *_args, **_kwargs: "")
    init_module._render_client_picker(console, detected, {"cursor", "claude-code"})
    rendered = stream.getvalue().strip().replace("\\", "/")

    fixture = Path(__file__).parents[1] / "fixtures" / "init_wizard" / "picker.txt"
    assert rendered == fixture.read_text(encoding="utf-8").strip().replace("\\", "/")
