# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time
from collections.abc import Callable

from vaner.broker.selector import select_artefacts
from vaner.models.artefact import Artefact
from vaner.models.retrieval_floor import (
    RetrievalFloorItem,
    RetrievalFloorRequest,
    RetrievalFloorResponse,
)
from vaner.policy.budget import count_tokens


RetrievalFloorProvider = Callable[[RetrievalFloorRequest], RetrievalFloorResponse]


def should_invoke_retrieval_floor(
    *,
    prediction_confidence: float,
    cache_tier: str,
    evidence_recall: float = 0.0,
    has_prepared_briefing: bool = False,
) -> bool:
    """Conservative gate for floor evidence.

    The floor is a reliability aid, not predictive credit. Strong ready
    predictions with evidence do not need it; weak/cold/missing evidence does.
    """

    normalized_tier = cache_tier.strip().lower()
    if normalized_tier in {"cold_miss", "miss"}:
        return True
    if not has_prepared_briefing:
        return True
    if prediction_confidence < 0.45:
        return True
    return evidence_recall <= 0.0 and normalized_tier in {"warm_start", "partial_hit"}


def builtin_retrieval_floor(
    request: RetrievalFloorRequest,
    artefacts: list[Artefact],
) -> RetrievalFloorResponse:
    """Small top-K floor over existing artefacts.

    This intentionally reuses Vaner's existing artefact summaries. It does not
    create an index, administer a vector DB, or try to become a RAG product.
    """

    started = time.monotonic()
    max_items = max(0, min(5, int(request.max_items)))
    max_tokens = max(0, int(request.max_tokens))
    selected = select_artefacts(request.query, artefacts, top_n=max_items)
    items: list[RetrievalFloorItem] = []
    used_tokens = 0
    for rank, artefact in enumerate(selected, start=1):
        excerpt = artefact.content.strip()
        item_tokens = count_tokens(excerpt)
        if max_tokens and used_tokens + item_tokens > max_tokens:
            remaining = max_tokens - used_tokens
            if remaining <= 0:
                break
            excerpt = excerpt[: max(0, remaining * 4)]
            item_tokens = count_tokens(excerpt)
        used_tokens += item_tokens
        items.append(
            RetrievalFloorItem(
                source_id=artefact.key,
                path_or_url=artefact.source_path,
                title=artefact.source_path.rsplit("/", 1)[-1],
                excerpt=excerpt,
                score=float(artefact.relevance_score),
                rank=rank,
                provenance="retrieval_floor",
                revision_or_hash=str(artefact.metadata.get("revision", "")),
                retrieved_at=time.time(),
            )
        )
    return RetrievalFloorResponse(
        provider_name="builtin_floor",
        provider_version="1",
        latency_ms=(time.monotonic() - started) * 1000.0,
        items=items,
    )


def detect_floor_conflicts(
    *,
    prediction_paths: list[str],
    floor_paths: list[str],
) -> list[str]:
    """Return human-readable conflict notes when evidence sets diverge."""

    prediction_set = {path for path in prediction_paths if path}
    floor_set = {path for path in floor_paths if path}
    if not prediction_set or not floor_set:
        return []
    overlap = prediction_set & floor_set
    if overlap:
        return []
    return [
        "prediction_evidence and retrieval_floor_evidence selected disjoint source paths",
    ]
