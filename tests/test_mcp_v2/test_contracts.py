from __future__ import annotations

import pytest
from pydantic import ValidationError

from vaner.mcp.contracts import (
    CACHE_TIER_TO_PROVENANCE,
    Abstain,
    MemoryMeta,
    Provenance,
    Resolution,
)


def test_cache_tier_mapping_covers_all_vaner_tiers() -> None:
    assert set(CACHE_TIER_TO_PROVENANCE) == {"full_hit", "partial_hit", "warm_start", "miss"}


def test_resolution_requires_resolution_id() -> None:
    with pytest.raises(ValidationError):
        Resolution(
            intent="x",
            confidence=0.5,
            summary="s",
            provenance=Provenance(mode="retrieval_fallback"),
        )


def test_abstain_is_disjoint_from_resolution() -> None:
    payload = Abstain(reason="low_confidence", message="no").model_dump(mode="json")
    assert "evidence" not in payload


def test_memory_meta_attaches_to_provenance() -> None:
    prov = Provenance(
        mode="predictive_hit",
        memory=MemoryMeta(
            state="trusted",
            confidence=0.9,
            last_validated_at=1.0,
            evidence_count=3,
        ),
    )
    assert prov.model_dump(mode="json")["memory"]["state"] == "trusted"


def test_abstain_supports_memory_conflict_reason() -> None:
    payload = Abstain(reason="memory_conflict", message="conflict").model_dump(mode="json")
    assert payload["reason"] == "memory_conflict"
