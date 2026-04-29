# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from vaner.broker.answerable import build_answerable_briefing
from vaner.models.artefact import Artefact, ArtefactKind


def _artefact(path: str, content: str, *, key: str | None = None) -> Artefact:
    return Artefact(
        key=key or f"file_summary:{path}",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path=path,
        source_mtime=time.time(),
        generated_at=time.time(),
        model="test",
        content=content,
    )


def test_answerable_briefing_ranks_direct_english_security_docs_first():
    briefing = build_answerable_briefing(
        "Walk me through the first-steps security example — what's the simplest scheme supported?",
        [
            _artefact(
                "docs/zh/docs/tutorial/security/first-steps.md",
                "FastAPI security first steps OAuth2 password bearer token authentication.",
            ),
            _artefact(
                "docs/en/docs/tutorial/security/first-steps.md",
                "FastAPI security first steps. OAuth2PasswordBearer is the simplest security scheme shown first.",
            ),
        ],
        max_tokens=600,
    )

    assert briefing.metadata.answerability == "full"
    assert briefing.sections[0].kind == "direct_answer_evidence"
    assert briefing.sections[0].items[0].path == "docs/en/docs/tutorial/security/first-steps.md"
    assert briefing.metadata.primary_paths[0] == "docs/en/docs/tutorial/security/first-steps.md"
    assert briefing.answer_plan.startswith("Use docs/en/docs/tutorial/security/first-steps.md")


def test_answerable_briefing_marks_none_for_irrelevant_evidence():
    briefing = build_answerable_briefing(
        "Where are Flask blueprint error handlers documented?",
        [_artefact("docs/en/docs/tutorial/security/first-steps.md", "FastAPI OAuth2 password bearer token authentication.")],
        max_tokens=500,
    )

    assert briefing.metadata.answerability == "none"
    assert briefing.metadata.direct_evidence_count == 0
    assert "No strong evidence" in briefing.answer_plan


def test_answerable_briefing_tracks_truncation_risk():
    content = "Dependency Injection\n" + "\n".join("Depends example path operation dependency." for _ in range(400))
    briefing = build_answerable_briefing(
        "Where does the official documentation introduce dependency injection?",
        [_artefact("docs/en/docs/tutorial/dependencies/index.md", content)],
        max_tokens=120,
    )

    assert briefing.metadata.truncation_applied is True
    assert briefing.metadata.truncation_risk in {"medium", "high"}
    assert briefing.metadata.answerability in {"weak", "full"}


def test_evidence_assembly_shadow_records_decisions_without_changing_text():
    artefacts = [
        _artefact(
            "docs/en/docs/tutorial/security/first-steps.md",
            "FastAPI security first steps. OAuth2PasswordBearer is the simplest security scheme shown first.",
        ),
        _artefact(
            "docs/en/docs/tutorial/security/first-steps.md",
            "FastAPI security first steps. OAuth2PasswordBearer is the simplest security scheme shown first.",
            key="duplicate",
        ),
    ]

    baseline = build_answerable_briefing(
        "Walk me through the first-steps security example.",
        artefacts,
        max_tokens=600,
        assembly_mode="off",
    )
    shadow = build_answerable_briefing(
        "Walk me through the first-steps security example.",
        artefacts,
        max_tokens=600,
        assembly_mode="shadow",
    )

    assert shadow.text == baseline.text
    assembly = shadow.metadata.evidence_assembly
    assert assembly.assembly_mode == "shadow"
    assert assembly.items_deduped == 1
    assert any(decision.decision == "dedupe" for decision in assembly.decisions)
    assert any(decision.would_change_output_in_shadow for decision in assembly.decisions)


def test_evidence_assembly_safe_dedupes_without_removing_unique_direct_evidence():
    duplicate_content = "FastAPI security first steps. OAuth2PasswordBearer is the simplest security scheme shown first."
    briefing = build_answerable_briefing(
        "Walk me through the first-steps security example.",
        [
            _artefact("docs/en/docs/tutorial/security/first-steps.md", duplicate_content),
            _artefact("docs/en/docs/tutorial/security/first-steps.md", duplicate_content, key="duplicate"),
            _artefact(
                "docs/en/docs/tutorial/security/oauth2-jwt.md",
                "FastAPI security OAuth2 JWT tutorial gives supporting token details.",
            ),
        ],
        max_tokens=800,
        assembly_mode="safe",
    )

    direct_paths = [item.path for item in briefing.sections[0].items]
    assert direct_paths.count("docs/en/docs/tutorial/security/first-steps.md") == 1
    assert "docs/en/docs/tutorial/security/oauth2-jwt.md" in direct_paths
    assert briefing.metadata.evidence_assembly.items_omitted_duplicate == 1
    assert briefing.metadata.evidence_assembly.items_protected >= 1


def test_evidence_assembly_safe_labels_transport_limited_context():
    briefing = build_answerable_briefing(
        "Where does the official documentation introduce dependency injection?",
        [
            _artefact(
                "docs/en/docs/tutorial/dependencies/index.md",
                "Dependency Injection\n" + "\n".join("Depends example path operation dependency." for _ in range(160)),
            ),
            _artefact(
                "docs/en/docs/tutorial/dependencies/classes-as-dependencies.md",
                "Classes as dependencies. Dependency injection can use classes as callables.",
            ),
        ],
        max_tokens=120,
        assembly_mode="safe",
    )

    assembly = briefing.metadata.evidence_assembly
    assert assembly.technical_limit_hit == "answerable_briefing_transport_limit"
    assert assembly.items_transport_limited >= 1
    assert any(decision.decision == "transport_limited" for decision in assembly.decisions)
    assert briefing.sections[0].items
