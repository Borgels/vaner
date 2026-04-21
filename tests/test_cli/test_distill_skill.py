from __future__ import annotations

import json
import time

import pytest
from typer.testing import CliRunner

from vaner.cli.commands import app

runner = CliRunner()


def test_distill_skill_command_writes_skill(temp_repo):
    help_result = runner.invoke(app.app, ["--help"])
    if "distill-skill" not in help_result.stdout:
        pytest.skip("distill-skill command unavailable on this CLI surface")
    decisions_dir = temp_repo / ".vaner" / "runtime" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    decision_id = "dec_123"
    payload = {
        "id": decision_id,
        "prompt": "investigate flaky test",
        "prompt_hash": "abc",
        "assembled_at": time.time(),
        "cache_tier": "full_hit",
        "partial_similarity": 0.0,
        "token_budget": 4000,
        "token_used": 300,
        "selections": [
            {
                "artefact_key": "file_summary:tests/test_api.py",
                "source_path": "tests/test_api.py",
                "final_score": 0.9,
                "token_count": 120,
                "stale": False,
                "kept": True,
                "drop_reason": None,
                "rationale": "contains failing assertion",
                "factors": [],
            }
        ],
        "prediction_links": {},
        "notes": [],
    }
    (decisions_dir / f"{decision_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    result = runner.invoke(app.app, ["distill-skill", decision_id, "--path", str(temp_repo)])
    assert result.exit_code == 0
    output = temp_repo / ".cursor" / "skills" / "vaner-distilled" / "investigate-flaky-test" / "SKILL.md"
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "x-vaner-managed: true" in text
    assert "Source decision id" in text
