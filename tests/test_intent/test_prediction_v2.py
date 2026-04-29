# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.prediction_v2 import compatibility_for_query, structured_from_prediction_fields


def test_draft_ready_requires_action_target_shape_and_fresh_evidence() -> None:
    structured = structured_from_prediction_fields(
        label="Add parser tests",
        description="Write unit coverage for parser behavior",
        anchor="src/parser.py",
        evidence_targets=("src/parser.py", "tests/test_parser.py"),
        readiness_mode="draft_ready",
        confidence=0.8,
    )

    result = compatibility_for_query("add parser tests", structured)

    assert result.compatible
    assert result.action_score == 1.0
    assert result.answer_shape_score == 1.0


def test_draft_ready_rejects_wrong_action_even_with_label_overlap() -> None:
    structured = structured_from_prediction_fields(
        label="Add parser tests",
        description="Write unit coverage for parser behavior",
        anchor="src/parser.py",
        evidence_targets=("src/parser.py",),
        readiness_mode="draft_ready",
        confidence=0.8,
    )

    result = compatibility_for_query("explain parser tests", structured)

    assert not result.compatible
    assert result.action_score < 0.9


def test_evidence_ready_is_less_strict_than_draft_ready() -> None:
    structured = structured_from_prediction_fields(
        label="Investigate parser",
        description="Inspect parser behavior",
        anchor="src/parser.py",
        evidence_targets=("src/parser.py",),
        readiness_mode="evidence_ready",
        confidence=0.6,
    )

    result = compatibility_for_query("explain parser behavior", structured)

    assert result.compatible


def test_contradictory_signal_blocks_compatibility() -> None:
    structured = structured_from_prediction_fields(
        label="Add parser tests",
        anchor="src/parser.py",
        evidence_targets=("src/parser.py",),
        readiness_mode="draft_ready",
        confidence=0.8,
    )

    result = compatibility_for_query("add parser tests", structured, invalidation_reason="file_change")

    assert not result.compatible
    assert result.freshness_score == 0.0


def test_draft_ready_demotes_concrete_object_without_exact_evidence() -> None:
    structured = structured_from_prediction_fields(
        label="Implement ArtefactStore",
        description="Add the concrete store implementation",
        anchor="cache cluster",
        evidence_targets=("src/cache.py", "docs/storage.md"),
        readiness_mode="draft_ready",
        confidence=0.8,
    )

    assert structured.readiness_mode == "evidence_ready"
    assert structured.abstain_reason == "weak_component_match"
    assert structured.readiness_reason == "weak_component_match"


def test_docs_contract_prediction_can_remain_draft_ready_on_doc_evidence() -> None:
    structured = structured_from_prediction_fields(
        label="Document ArtefactStore schema contract",
        description="Write docs for the serialized schema contract",
        anchor="docs/storage.md",
        evidence_targets=("docs/storage.md",),
        readiness_mode="draft_ready",
        confidence=0.8,
    )

    assert structured.readiness_mode == "draft_ready"
