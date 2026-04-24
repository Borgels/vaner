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


@pytest.mark.asyncio
async def test_tiered_cache_prefers_canonical_units_over_legacy(tmp_path: Path):
    store = ArtefactStore(tmp_path / "cache_canonical.db")
    await store.initialize()
    cache = TieredPredictionCache(store)

    # Canonical keys intentionally disagree with legacy keys.
    #
    # ``exploration_source`` must be set to mark this as an intentional
    # prediction entry. Without it, ``match()`` classifies the record as a
    # query write-through (prior-turn selections persisted as a cache
    # side-effect) and applies a 0.55 discount to deprioritise it — that
    # penalty is load-bearing and documented at cache.py:219-226. The test
    # is about canonical-vs-legacy-unit preference, not the write-through
    # penalty, so we opt into the intentional-prediction branch.
    await cache.store_entry(
        prompt_hint="explain sample module",
        package=None,
        enrichment={
            "anchor_units": ["sample.py"],
            "anchor_files": ["legacy_wrong.py"],
            "source_units": ["sample_helper.py"],
            "source_paths": ["legacy_source_wrong.py"],
            "exploration_source": "llm_branch",
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
