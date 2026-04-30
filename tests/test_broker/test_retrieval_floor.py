# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from vaner.broker.retrieval_floor import (
    builtin_retrieval_floor,
    detect_floor_conflicts,
    should_invoke_retrieval_floor,
)
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.retrieval_floor import RetrievalFloorRequest


def _artefact(key: str, path: str, content: str) -> Artefact:
    now = time.time()
    return Artefact(
        key=key,
        kind=ArtefactKind.FILE_SUMMARY,
        source_path=path,
        source_mtime=now,
        generated_at=now,
        model="test",
        content=content,
    )


def test_retrieval_floor_gate_invokes_on_cold_or_weak_prediction():
    assert should_invoke_retrieval_floor(
        prediction_confidence=0.9,
        cache_tier="cold_miss",
        evidence_recall=1.0,
        has_prepared_briefing=True,
    )
    assert should_invoke_retrieval_floor(
        prediction_confidence=0.2,
        cache_tier="warm_start",
        evidence_recall=0.0,
        has_prepared_briefing=True,
    )
    assert not should_invoke_retrieval_floor(
        prediction_confidence=0.9,
        cache_tier="full_hit",
        evidence_recall=1.0,
        has_prepared_briefing=True,
    )


def test_builtin_retrieval_floor_is_bounded_and_labeled():
    response = builtin_retrieval_floor(
        RetrievalFloorRequest(query="bubble sort", max_items=1, max_tokens=20),
        [
            _artefact("a", "sorts/bubble_sort.py", "bubble sort implementation " * 20),
            _artefact("b", "sorts/quick_sort.py", "quick sort implementation"),
        ],
    )

    assert response.provider_name == "builtin_floor"
    assert len(response.items) == 1
    assert response.items[0].provenance == "retrieval_floor"
    assert response.items[0].path_or_url == "sorts/bubble_sort.py"


def test_detect_floor_conflicts_marks_disjoint_evidence():
    assert detect_floor_conflicts(
        prediction_paths=["src/predicted.py"],
        floor_paths=["docs/retrieved.md"],
    ) == ["prediction_evidence and retrieval_floor_evidence selected disjoint source paths"]
    assert (
        detect_floor_conflicts(
            prediction_paths=["src/shared.py"],
            floor_paths=["src/shared.py"],
        )
        == []
    )
