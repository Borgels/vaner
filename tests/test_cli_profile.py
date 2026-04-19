# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from vaner.cli.main import app


def _init_repo(runner: CliRunner, repo: Path) -> None:
    result = runner.invoke(app, ["init", "--path", str(repo)])
    assert result.exit_code == 0, result.output


def test_profile_show_snapshot_includes_expected_sections(tmp_path: Path):
    runner = CliRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    _init_repo(runner, repo)

    result = runner.invoke(app, ["profile", "show", "--path", str(repo)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "pins" in payload
    assert "prompt_macros" in payload
    assert "habit_transitions" in payload
    assert "workflow_phase" in payload
    assert "explored_scenarios" in payload


def test_profile_pin_unpin_round_trip(tmp_path: Path):
    runner = CliRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    _init_repo(runner, repo)

    pin_result = runner.invoke(
        app,
        ["profile", "pin", "prefer_source=arc", "--scope", "user", "--path", str(repo)],
    )
    assert pin_result.exit_code == 0, pin_result.output

    show_after_pin = runner.invoke(app, ["profile", "show", "--path", str(repo)])
    payload = json.loads(show_after_pin.output)
    assert any(row["key"] == "prefer_source" and row["value"] == "arc" for row in payload["pins"])

    unpin_result = runner.invoke(app, ["profile", "unpin", "prefer_source", "--path", str(repo)])
    assert unpin_result.exit_code == 0, unpin_result.output

    show_after_unpin = runner.invoke(app, ["profile", "show", "--path", str(repo)])
    payload_after = json.loads(show_after_unpin.output)
    assert not any(row["key"] == "prefer_source" for row in payload_after["pins"])


def test_profile_export_import_parity(tmp_path: Path):
    runner = CliRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    _init_repo(runner, repo)

    assert runner.invoke(app, ["profile", "pin", "focus_paths=app/**", "--scope", "project", "--path", str(repo)]).exit_code == 0
    assert runner.invoke(app, ["profile", "pin", "prefer_source=arc", "--scope", "user", "--path", str(repo)]).exit_code == 0

    export_file = repo / ".vaner" / "profile.env"
    export_result = runner.invoke(
        app,
        ["profile", "export", "--out", str(export_file), "--path", str(repo)],
    )
    assert export_result.exit_code == 0, export_result.output
    exported_text = export_file.read_text(encoding="utf-8")
    assert "prefer_source=arc" in exported_text
    assert "project:focus_paths=app/**" in exported_text

    assert runner.invoke(app, ["profile", "unpin", "focus_paths", "--path", str(repo)]).exit_code == 0
    assert runner.invoke(app, ["profile", "unpin", "prefer_source", "--path", str(repo)]).exit_code == 0

    import_result = runner.invoke(
        app,
        ["profile", "import", str(export_file), "--path", str(repo)],
    )
    assert import_result.exit_code == 0, import_result.output

    show_after_import = runner.invoke(app, ["profile", "show", "--path", str(repo)])
    payload = json.loads(show_after_import.output)
    restored = {(row["scope"], row["key"], row["value"]) for row in payload["pins"]}
    assert ("project", "focus_paths", "app/**") in restored
    assert ("user", "prefer_source", "arc") in restored
