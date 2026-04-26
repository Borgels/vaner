# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio

import pytest

from vaner.memory.policy import InvalidMemoryTransition, ReuseInput, decide_reuse
from vaner.models.scenario import Scenario
from vaner.store.scenarios.sqlite import ScenarioStore


def test_stale_memory_from_store_is_not_reused_as_predictive_hit(temp_repo) -> None:
    async def _run() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_stale_reuse_guard",
                kind="debug",
                score=0.9,
                confidence=0.8,
                entities=["router"],
                prepared_context="Router path",
                memory_state="trusted",
                memory_confidence=0.9,
                memory_evidence_hashes_json='["fp_old","fp_kept"]',
                pinned=1,
            )
        )

        await store.mark_stale_by_evidence("scn_stale_reuse_guard", evidence_hashes_now=["fp_kept"])
        scenario = await store.get("scn_stale_reuse_guard")

        assert scenario is not None
        assert scenario.memory_state == "stale"
        assert scenario.pinned == 0
        assert (
            decide_reuse(
                ReuseInput(
                    evidence_fresh=True,
                    envelope_similarity=0.99,
                    contradiction_since_last_validation=False,
                    memory_state=scenario.memory_state,
                )
            )
            == "ignore_prior"
        )

    asyncio.run(_run())


def test_demoted_store_state_cannot_jump_directly_to_trusted(temp_repo) -> None:
    async def _run() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_demoted_guard",
                kind="debug",
                score=0.5,
                confidence=0.5,
                entities=["auth"],
                prepared_context="Auth path",
                memory_state="demoted",
                memory_confidence=0.2,
            )
        )

        with pytest.raises(InvalidMemoryTransition):
            await store.promote_scenario(
                "scn_demoted_guard",
                new_state="trusted",
                confidence=0.9,
                evidence_hashes=["fp"],
                at=123.0,
            )

    asyncio.run(_run())
