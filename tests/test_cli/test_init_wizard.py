# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from vaner.cli.commands import init as init_module
from vaner.cli.commands.app import app
from vaner.cli.commands.mcp_clients import ClientSpec, ClientStatus, DetectedClient, WriteResult

runner = CliRunner()

if not hasattr(init_module, "detect_all"):
    pytest.skip("init wizard helpers unavailable on this branch surface", allow_module_level=True)


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


def test_non_interactive_skip_all(temp_repo, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(init_module, "detect_all", lambda _repo_root: [_detected("cursor")])
    monkeypatch.setattr(
        init_module,
        "write_client",
        lambda *args, **kwargs: calls.append("called") or WriteResult(client_id="cursor", path=None, action="added"),
    )
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(temp_repo),
            "--no-interactive",
            "--clients",
            "none",
            "--backend-preset",
            "skip",
            "--compute-preset",
            "background",
        ],
    )
    assert result.exit_code == 0
    assert calls == []


def test_non_interactive_auto_writes_detected(temp_repo, monkeypatch) -> None:
    detected = [_detected("cursor"), _detected("claude-code"), _detected("codex-cli", ClientStatus.MISSING)]
    calls: list[str] = []
    monkeypatch.setattr(init_module, "detect_all", lambda _repo_root: detected)

    def _fake_write(item, **_kwargs):  # noqa: ANN001
        calls.append(item.spec.id)
        return WriteResult(client_id=item.spec.id, path=item.path, action="added")

    monkeypatch.setattr(init_module, "write_client", _fake_write)
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(temp_repo),
            "--no-interactive",
            "--clients",
            "auto",
            "--backend-preset",
            "skip",
        ],
    )
    assert result.exit_code == 0
    assert calls == ["cursor", "claude-code"]


def test_non_interactive_csv_selects_exactly_those(temp_repo, monkeypatch) -> None:
    detected = [_detected("cursor"), _detected("claude-code"), _detected("codex-cli")]
    calls: list[str] = []
    monkeypatch.setattr(init_module, "detect_all", lambda _repo_root: detected)

    def _fake_write(item, **_kwargs):  # noqa: ANN001
        calls.append(item.spec.id)
        return WriteResult(client_id=item.spec.id, path=item.path, action="added")

    monkeypatch.setattr(init_module, "write_client", _fake_write)
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(temp_repo),
            "--no-interactive",
            "--clients",
            "cursor,claude-code",
            "--backend-preset",
            "skip",
        ],
    )
    assert result.exit_code == 0
    assert calls == ["cursor", "claude-code"]


def test_dry_run_writes_nothing(temp_repo, monkeypatch) -> None:
    detected = [_detected("cursor")]
    observed_dry_run: list[bool] = []
    monkeypatch.setattr(init_module, "detect_all", lambda _repo_root: detected)

    def _fake_write(item, **kwargs):  # noqa: ANN001
        observed_dry_run.append(bool(kwargs.get("dry_run")))
        return WriteResult(client_id=item.spec.id, path=item.path, action="added")

    monkeypatch.setattr(init_module, "write_client", _fake_write)
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(temp_repo),
            "--no-interactive",
            "--clients",
            "cursor",
            "--backend-preset",
            "skip",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert observed_dry_run == [True]
    assert "No files modified (dry-run)." in result.stdout


def test_cloud_cost_gate_blocks_without_flag(temp_repo) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(temp_repo),
            "--backend-preset",
            "openai",
            "--no-interactive",
        ],
    )
    assert result.exit_code == 1
    assert "--accept-cloud-costs" in result.stderr


def test_interactive_picker_accepts_enter_for_default(temp_repo, monkeypatch) -> None:
    detected = [_detected("cursor"), _detected("claude-code"), _detected("codex-cli", ClientStatus.MISSING)]
    calls: list[str] = []
    monkeypatch.setattr(init_module, "detect_all", lambda _repo_root: detected)

    def _fake_write(item, **_kwargs):  # noqa: ANN001
        calls.append(item.spec.id)
        return WriteResult(client_id=item.spec.id, path=item.path, action="added")

    monkeypatch.setattr(init_module, "write_client", _fake_write)
    result = runner.invoke(
        app,
        ["init", "--path", str(temp_repo), "--interactive", "--backend-preset", "skip", "--compute-preset", "background"],
        input="\nY\n",
    )
    assert result.exit_code == 0
    assert calls == ["cursor", "claude-code"]


def test_interactive_picker_parses_number_set(temp_repo, monkeypatch) -> None:
    detected = [_detected("cursor"), _detected("claude-code"), _detected("codex-cli")]
    calls: list[str] = []
    monkeypatch.setattr(init_module, "detect_all", lambda _repo_root: detected)

    def _fake_write(item, **_kwargs):  # noqa: ANN001
        calls.append(item.spec.id)
        return WriteResult(client_id=item.spec.id, path=item.path, action="added")

    monkeypatch.setattr(init_module, "write_client", _fake_write)
    result = runner.invoke(
        app,
        ["init", "--path", str(temp_repo), "--interactive", "--backend-preset", "skip", "--compute-preset", "background"],
        input="1 3\nY\n",
    )
    assert result.exit_code == 0
    assert calls == ["cursor", "codex-cli"]
