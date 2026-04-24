# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from vaner.cli.commands.integrations import integrations_app

runner = CliRunner()


def test_doctor_json_reports_guidance_version(tmp_path: Path) -> None:
    result = runner.invoke(
        integrations_app,
        [
            "doctor",
            "--repo-root",
            str(tmp_path),
            "--format",
            "json",
            "--daemon-url",
            "http://127.0.0.1:1",  # unreachable on purpose
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["guidance"]["version"] == 1
    assert data["integrations_config"]["context_injection"]["mode"] == "policy_hybrid"
    assert data["daemon"]["reachable"] is False
    assert data["handoff"]["present"] is False


def test_doctor_pretty_output_renders(tmp_path: Path) -> None:
    result = runner.invoke(
        integrations_app,
        [
            "doctor",
            "--repo-root",
            str(tmp_path),
            "--daemon-url",
            "http://127.0.0.1:1",
        ],
    )
    assert result.exit_code == 0
    assert "Vaner integrations doctor" in result.output
    assert "Integrations config" in result.output


def test_doctor_detects_pending_handoff(tmp_path: Path) -> None:
    vaner_dir = tmp_path / ".vaner"
    vaner_dir.mkdir()
    payload = {
        "adopted_from_prediction_id": "pred-xyz",
        "resolution_id": "adopt-pred-xyz",
        "adopted_at": "2026-04-25T12:00:00Z",
    }
    (vaner_dir / "pending-adopt.json").write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(
        integrations_app,
        [
            "doctor",
            "--repo-root",
            str(tmp_path),
            "--format",
            "json",
            "--daemon-url",
            "http://127.0.0.1:1",
        ],
    )
    data = json.loads(result.output)
    assert data["handoff"]["present"] is True
    assert data["handoff"]["adopted_from_prediction_id"] == "pred-xyz"


def test_tier_command_prints_guidance_version() -> None:
    result = runner.invoke(integrations_app, ["tier"])
    assert result.exit_code == 0
    assert "guidance_version=1" in result.output
