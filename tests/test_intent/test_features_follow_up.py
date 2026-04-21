from __future__ import annotations

import time
import uuid

import pytest

from vaner.intent.features import extract_hybrid_features
from vaner.models.signal import SignalEvent
from vaner.store.artefacts import ArtefactStore


@pytest.mark.asyncio
async def test_follow_up_offer_strength_defaults_to_zero(temp_repo) -> None:
    store = ArtefactStore(temp_repo / ".vaner" / "store.db")
    await store.initialize()

    features = await extract_hybrid_features(store, prompt=None)

    assert features["follow_up_offer_strength"] == 0.0


@pytest.mark.asyncio
async def test_follow_up_offer_strength_tracks_recent_acceptance(temp_repo) -> None:
    store = ArtefactStore(temp_repo / ".vaner" / "store.db")
    await store.initialize()
    now = time.time()

    await store.insert_signal_event(
        SignalEvent(
            id=str(uuid.uuid4()),
            source="proxy",
            kind="follow_up_offer_detected",
            timestamp=now,
            payload={
                "phrase_pattern_id": "would_you_like_me_to",
                "phrase_family": "offer",
                "prompt_macro": "general",
                "corpus_id": "default",
            },
        )
    )
    await store.insert_signal_event(
        SignalEvent(
            id=str(uuid.uuid4()),
            source="proxy",
            kind="follow_up_offer_accepted",
            timestamp=now,
            payload={
                "phrase_pattern_id": "would_you_like_me_to",
                "phrase_family": "offer",
                "prompt_macro": "general",
                "corpus_id": "default",
            },
        )
    )

    features = await extract_hybrid_features(store, prompt=None)

    assert features["follow_up_offer_strength"] > 0.9


@pytest.mark.asyncio
async def test_follow_up_offer_strength_decays_for_stale_events(temp_repo) -> None:
    store = ArtefactStore(temp_repo / ".vaner" / "store.db")
    await store.initialize()
    now = time.time()

    await store.insert_signal_event(
        SignalEvent(
            id=str(uuid.uuid4()),
            source="proxy",
            kind="follow_up_offer_detected",
            timestamp=now - 7200.0,
            payload={
                "phrase_pattern_id": "would_you_like_me_to",
                "phrase_family": "offer",
                "prompt_macro": "general",
                "corpus_id": "default",
            },
        )
    )
    await store.insert_signal_event(
        SignalEvent(
            id=str(uuid.uuid4()),
            source="proxy",
            kind="follow_up_offer_accepted",
            timestamp=now - 7200.0,
            payload={
                "phrase_pattern_id": "would_you_like_me_to",
                "phrase_family": "offer",
                "prompt_macro": "general",
                "corpus_id": "default",
            },
        )
    )

    features = await extract_hybrid_features(store, prompt=None)

    assert 0.0 < features["follow_up_offer_strength"] < 0.1
