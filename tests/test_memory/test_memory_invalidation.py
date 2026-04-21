from __future__ import annotations

import asyncio

from vaner.memory.policy import InvalidationContext, decide_invalidation
from vaner.models.scenario import Scenario
from vaner.store.scenarios.sqlite import ScenarioStore


def test_evidence_drift_downgrades_trusted_to_stale_on_next_resolve() -> None:
    decision = decide_invalidation(
        InvalidationContext(
            fingerprints_at_validation=["h1", "h2"],
            fingerprints_now=["h1"],
            memory_confidence=0.8,
        ),
        "trusted",
    )
    assert decision is not None
    assert decision.to_state == "stale"


def test_store_marks_trusted_memory_stale_on_fingerprint_drift(temp_repo) -> None:
    async def _run() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_drift",
                kind="debug",
                score=0.8,
                confidence=0.7,
                entities=["auth"],
                prepared_context="Auth path",
                memory_state="trusted",
                memory_confidence=0.8,
                memory_evidence_hashes_json='["fp_a","fp_b"]',
            )
        )
        await store.mark_stale_by_evidence("scn_drift", evidence_hashes_now=["fp_a"])
        updated = await store.get("scn_drift")
        assert updated is not None
        assert updated.memory_state == "stale"

    asyncio.run(_run())
