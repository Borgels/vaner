# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.assist_decision import (
    build_assist_decision,
    evaluate_prediction_relevance,
    normalize_prediction_label,
)


def _row(**overrides):
    row = {
        "id": "pred-1",
        "label": "Improve prediction relevance for primary-AI turns",
        "source": "arc",
        "source_label": "Recent work",
        "confidence": 0.83,
        "readiness": "ready",
        "trust_status": "ready",
        "freshness": "fresh",
        "diagnostic_status": "verified",
        "watched_sources": ["src/vaner/intent/prediction.py"],
        "has_draft": True,
        "has_briefing": True,
    }
    row.update(overrides)
    return row


def test_strong_match_requires_ready_material_and_multiple_signals() -> None:
    row = _row()

    relevance = evaluate_prediction_relevance(row, "Improve prediction relevance scoring for primary-AI turns")

    assert relevance.match_state == "strong_match"
    assert relevance.recommended_action == "adopt"
    assert "prompt overlap" in relevance.relevance_signals
    assert "workspace/source overlap" in relevance.relevance_signals


def test_broad_ready_prediction_is_not_adopted_from_score_alone() -> None:
    row = _row(
        label="Prepare engine follow-up",
        watched_sources=["src/vaner/engine.py"],
        confidence=0.97,
    )

    relevance = evaluate_prediction_relevance(row, "Update desktop controls for prepared work")

    assert relevance.match_state == "unrelated"
    assert relevance.recommended_action == "ignore"


def test_broad_artefact_category_is_not_strong_even_with_plan_overlap() -> None:
    row = _row(
        label="Prepare context for src/vaner",
        source="artefact_item",
        specificity="category",
        ui_summary="Artefact item under goal 'Architecture': The intent layer predicts likely next prompts and context targets.",
        watched_sources=[
            "src/vaner/engine.py",
            "src/vaner/intent/prediction_v2.py",
            "src/vaner/intent/prepared_work.py",
        ],
    )

    relevance = evaluate_prediction_relevance(
        row,
        "Review and harden the Vaner turn decision implementation and prediction payloads",
        context={"active_plan": "Review and harden the Vaner turn decision implementation and prediction payloads"},
    )

    assert relevance.match_state != "strong_match"
    assert relevance.recommended_action != "adopt"
    assert not relevance.display_label.endswith("src/vaner")


def test_active_plan_and_source_overlap_without_prompt_overlap_does_not_adopt() -> None:
    row = _row(
        label="src/vaner/engine.py: orchestration and runtime loop",
        source="artefact_item",
        specificity="concrete",
        watched_sources=["src/vaner/engine.py"],
    )

    relevance = evaluate_prediction_relevance(
        row,
        "Review unrelated desktop copy",
        context={"active_plan": "Review engine orchestration"},
    )

    assert relevance.match_state != "strong_match"
    assert relevance.recommended_action != "adopt"


def test_concrete_current_turn_file_match_is_adoptable() -> None:
    row = _row(
        label="Harden vaner.suggest no-wait timeout behavior",
        source="arc",
        specificity="concrete",
        watched_sources=["src/vaner/mcp/server.py"],
    )

    relevance = evaluate_prediction_relevance(row, "Harden vaner.suggest no-wait timeout behavior in mcp server")

    assert relevance.match_state == "strong_match"
    assert relevance.recommended_action == "adopt"


def test_composer_draft_can_strong_match_without_path_overlap() -> None:
    row = _row(
        label="Update prepared context wording",
        source="composer_intent",
        specificity="concrete",
        watched_sources=[],
    )

    relevance = evaluate_prediction_relevance(row, "Update prepared context wording")

    assert relevance.match_state == "strong_match"
    assert relevance.recommended_action == "adopt"


def test_stale_prediction_never_adopts_even_with_many_signals() -> None:
    row = _row(
        label="Harden vaner.suggest no-wait timeout behavior",
        readiness="stale",
        freshness="stale",
        trust_status="invalidated",
        diagnostic_status="invalidated",
        watched_sources=["src/vaner/mcp/server.py"],
    )

    relevance = evaluate_prediction_relevance(row, "Harden vaner.suggest no-wait timeout behavior in mcp server")

    assert relevance.match_state == "stale"
    assert relevance.recommended_action == "ignore"


def test_ready_without_payload_is_not_adoptable() -> None:
    row = _row(has_draft=False, has_briefing=False)

    relevance = evaluate_prediction_relevance(row, "Improve prediction relevance scoring")

    assert relevance.recommended_action != "adopt"
    assert relevance.match_state in {"weak_match", "unrelated"}


def test_bad_internal_labels_are_suppressed_for_normal_surfaces() -> None:
    assert normalize_prediction_label(_row(label="Goal: X")) != "Goal: X"
    assert normalize_prediction_label(_row(label="Goal: Tmp")) != "Goal: Tmp"
    bad_step = _row(label="Step: Internal category fragment without a complete human label")
    assert not normalize_prediction_label(bad_step).startswith("Step:")


def test_decision_adopts_only_strong_match() -> None:
    strong = _row()
    relevance = evaluate_prediction_relevance(strong, "Improve prediction relevance scoring for primary-AI turns")
    strong.update(relevance.as_dict())
    weak = _row(id="pred-2", label="Prepare desktop window copy", watched_sources=["ui/desktop/App.tsx"])
    weak_relevance = evaluate_prediction_relevance(weak, "Improve prediction relevance scoring for primary-AI turns")
    weak.update(weak_relevance.as_dict())

    decision = build_assist_decision("Improve prediction relevance scoring for primary-AI turns", [weak, strong])

    assert decision.action == "adopt_prediction"
    assert decision.prediction_id == "pred-1"


def test_decision_answers_normally_when_engine_is_unavailable() -> None:
    decision = build_assist_decision(
        "Refactor a small helper",
        [],
        has_retrieval_candidate=True,
        engine_unavailable=True,
    )

    assert decision.action == "answer_normally"


def test_decision_resolve_is_optional_not_default_fallback() -> None:
    cold = build_assist_decision("Refactor a small helper", [])
    retrieval = build_assist_decision("Where should auth middleware be changed", [], has_retrieval_candidate=True)

    assert cold.action == "answer_normally"
    assert retrieval.action == "resolve_optional"
