from __future__ import annotations

import time
import uuid

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.models.signal import SignalEvent


async def _emit_follow_up_pair(
    engine: VanerEngine,
    *,
    pattern_id: str,
    phrase_family: str,
    prompt_macro: str,
    accepted: bool,
) -> None:
    base_payload = {
        "phrase_pattern_id": pattern_id,
        "phrase_family": phrase_family,
        "prompt_macro": prompt_macro,
        "corpus_id": "repo",
        "action": "run tests next",
    }
    now = time.time()
    await engine.store.insert_signal_event(
        SignalEvent(
            id=str(uuid.uuid4()),
            source="proxy",
            kind="follow_up_offer_detected",
            timestamp=now,
            payload=base_payload,
        )
    )
    if accepted:
        await engine.store.insert_signal_event(
            SignalEvent(
                id=str(uuid.uuid4()),
                source="proxy",
                kind="follow_up_offer_accepted",
                timestamp=now,
                payload=base_payload,
            )
        )


@pytest.mark.asyncio
async def test_follow_up_promotion_gate_requires_three_acceptances(temp_repo) -> None:
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()

    for _ in range(2):
        await _emit_follow_up_pair(
            engine,
            pattern_id="would_you_like_me_to",
            phrase_family="offer",
            prompt_macro="general",
            accepted=True,
        )
    await engine._refresh_follow_up_pattern_memory()
    rows = await engine.store.list_pinned_facts(scope="workflow")
    assert not [row for row in rows if str(row.get("key", "")).startswith("follow_up_pattern:")]

    await _emit_follow_up_pair(
        engine,
        pattern_id="would_you_like_me_to",
        phrase_family="offer",
        prompt_macro="general",
        accepted=True,
    )
    await engine._refresh_follow_up_pattern_memory()
    rows = await engine.store.list_pinned_facts(scope="workflow")
    promoted = [row for row in rows if str(row.get("key", "")) == "follow_up_pattern:would_you_like_me_to:general"]
    assert promoted
    assert promoted[0]["scoring_hint"]["state"] == "trusted"


@pytest.mark.asyncio
async def test_follow_up_pattern_is_superseded_and_marked_stale(temp_repo) -> None:
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()

    for _ in range(3):
        await _emit_follow_up_pair(
            engine,
            pattern_id="would_you_like_me_to",
            phrase_family="offer",
            prompt_macro="general",
            accepted=True,
        )
    await engine._refresh_follow_up_pattern_memory()

    # Higher acceptance-rate pattern in same family should supersede older trusted one.
    for _ in range(4):
        await _emit_follow_up_pair(
            engine,
            pattern_id="shall_i",
            phrase_family="offer",
            prompt_macro="general",
            accepted=True,
        )
    for _ in range(3):
        await _emit_follow_up_pair(
            engine,
            pattern_id="would_you_like_me_to",
            phrase_family="offer",
            prompt_macro="general",
            accepted=False,
        )

    await engine._refresh_follow_up_pattern_memory()
    rows = await engine.store.list_pinned_facts(scope="workflow")
    facts = {str(row["key"]): row for row in rows if str(row.get("key", "")).startswith("follow_up_pattern:")}

    assert facts["follow_up_pattern:shall_i:general"]["scoring_hint"]["state"] == "trusted"
    assert facts["follow_up_pattern:would_you_like_me_to:general"]["scoring_hint"]["state"] == "stale"
