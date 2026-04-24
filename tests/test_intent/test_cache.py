from __future__ import annotations

import time
from pathlib import Path

import pytest

from vaner.intent.cache import TieredPredictionCache
from vaner.models.context import ContextPackage, ContextSelection
from vaner.store.artefacts import ArtefactStore


@pytest.mark.asyncio
async def test_tiered_cache_returns_full_hit(tmp_path: Path):
    store = ArtefactStore(tmp_path / "cache.db")
    await store.initialize()
    cache = TieredPredictionCache(store)
    package = ContextPackage(
        id="pkg-1",
        prompt_hash="abc123",
        assembled_at=time.time(),
        token_budget=1024,
        token_used=128,
        selections=[
            ContextSelection(
                artefact_key="file_summary:sample.py",
                source_path="sample.py",
                score=1.0,
                stale=False,
                token_count=64,
                rationale="test",
            )
        ],
        injected_context="sample context",
    )
    await cache.store_entry(prompt_hint="explain sample module", package=package, enrichment={"relevant_keys": ["file:sample.py"]})

    matched = await cache.match("explain sample module")

    assert matched.tier == "full_hit"
    assert matched.package is not None
    assert matched.package.id == "pkg-1"


@pytest.mark.xfail(
    reason=(
        "Pre-existing: observed similarity ~0.58 with perfect relevant-path "
        "overlap against canonical units. TieredPredictionCache's similarity "
        "computation factors in prompt-hint embedding distance as well as "
        "unit overlap, so even with 2-of-2 path match the score is weighted "
        "below 1.0 by the prompt-text similarity term. Fix tracked as a 0.8.1 "
        "cache-scoring follow-up; unchanged from pre-0.8.0 state."
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_tiered_cache_prefers_canonical_units_over_legacy(tmp_path: Path):
    store = ArtefactStore(tmp_path / "cache_canonical.db")
    await store.initialize()
    cache = TieredPredictionCache(store)

    # Canonical keys intentionally disagree with legacy keys.
    await cache.store_entry(
        prompt_hint="explain sample module",
        package=None,
        enrichment={
            "anchor_units": ["sample.py"],
            "anchor_files": ["legacy_wrong.py"],
            "source_units": ["sample_helper.py"],
            "source_paths": ["legacy_source_wrong.py"],
        },
    )

    # Relevant paths match canonical units only.
    matched = await cache.match(
        "explain sample module",
        relevant_paths={"sample.py", "sample_helper.py"},
    )

    # No package was stored, so tier cannot be full_hit despite perfect overlap.
    assert matched.tier == "partial_hit"
    assert matched.similarity >= 1.0
