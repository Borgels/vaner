# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import time
from pathlib import Path

from typer.testing import CliRunner

from vaner.broker.selector import select_artefacts
from vaner.cli.main import app
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.context import ContextPackage
from vaner.models.decision import DecisionRecord, PredictionLink, ScoreFactor, SelectionDecision


def _sample_record() -> DecisionRecord:
    record = DecisionRecord(
        id="ctx_test123",
        prompt="where is auth enforced?",
        prompt_hash="abc123",
        assembled_at=time.time(),
        cache_tier="partial_hit",
        partial_similarity=0.78,
        token_budget=4096,
        token_used=1200,
        selections=[
            SelectionDecision(
                artefact_key="file_summary:src/auth.py",
                source_path="src/auth.py",
                final_score=6.4,
                token_count=600,
                stale=False,
                kept=True,
                rationale="intent_score+preferred_path",
                factors=[
                    ScoreFactor(name="intent_score", contribution=5.1, detail="intent model"),
                    ScoreFactor(name="preferred_path", contribution=0.8, detail="recent git activity"),
                ],
            )
        ],
        prediction_links={
            "file_summary:src/auth.py": PredictionLink(
                source="frontier/llm_branch",
                scenario_question="How is auth enforced?",
                scenario_rationale="fallback_uncovered_path",
                confidence=0.72,
            )
        },
    )
    return record


def test_selector_capture_respects_privacy_exclusion() -> None:
    now = time.time()
    public = Artefact(
        key="file_summary:public.py",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="public.py",
        source_mtime=now,
        generated_at=now,
        model="test",
        content="auth token validator",
        metadata={"privacy_zone": "local"},
    )
    private = Artefact(
        key="file_summary:private.py",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="private.py",
        source_mtime=now,
        generated_at=now,
        model="test",
        content="auth token validator",
        metadata={"privacy_zone": "private_local"},
    )
    factors: dict[str, list[ScoreFactor]] = {}
    drop_reasons: dict[str, str] = {}
    selected = select_artefacts(
        "where is auth enforced",
        [public, private],
        top_n=8,
        exclude_private=True,
        capture_factors=factors,
        capture_drop_reasons=drop_reasons,
    )
    assert [item.key for item in selected] == ["file_summary:public.py"]
    assert "file_summary:public.py" in factors
    assert drop_reasons["file_summary:private.py"] == "privacy_excluded"


def test_query_explain_outputs_decision_block(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".vaner" / "runtime").mkdir(parents=True, exist_ok=True)
    record = _sample_record()

    def _fake_query(prompt: str, repo_root: Path) -> ContextPackage:  # noqa: ARG001
        return ContextPackage(
            id=record.id,
            prompt_hash=record.prompt_hash,
            assembled_at=record.assembled_at,
            token_budget=record.token_budget,
            token_used=record.token_used,
            selections=[],
            injected_context="Injected context",
        )

    monkeypatch.setattr("vaner.cli.commands.app_legacy.api.query", _fake_query)
    monkeypatch.setattr("vaner.cli.commands.app_legacy.api.inspect_last_decision", lambda _repo: record)
    result = runner.invoke(app, ["query", "where is auth enforced?", "--path", str(repo), "--explain"])
    assert result.exit_code == 0, result.output
    assert "Injected context" in result.output
    assert "decision: ctx_test123" in result.output
    assert "predicted by: frontier/llm_branch" in result.output

    json_result = runner.invoke(app, ["query", "where is auth enforced?", "--path", str(repo), "--explain", "--json"])
    assert json_result.exit_code == 0, json_result.output
    json_start = json_result.output.find("{")
    assert json_start >= 0
    parsed = DecisionRecord.model_validate_json(json_result.output[json_start:])
    assert parsed.cache_tier == "partial_hit"


def test_why_list_and_json_outputs(tmp_path: Path) -> None:
    runner = CliRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    record = _sample_record()
    record.write(repo)

    list_result = runner.invoke(app, ["why", "--list", "--path", str(repo)])
    assert list_result.exit_code == 0, list_result.output
    assert "ctx_test123" in list_result.output

    json_result = runner.invoke(app, ["why", "ctx_test123", "--json", "--path", str(repo)])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["id"] == "ctx_test123"
