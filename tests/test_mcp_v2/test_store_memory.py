from __future__ import annotations

import asyncio
import json
import sqlite3
import time

from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore


def test_initialize_idempotent_on_existing_db(tmp_path) -> None:
    db_path = tmp_path / ".vaner" / "scenarios.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE scenarios (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                score REAL NOT NULL,
                confidence REAL NOT NULL,
                entities_json TEXT NOT NULL,
                prepared_context TEXT NOT NULL,
                coverage_gaps_json TEXT NOT NULL,
                freshness TEXT NOT NULL,
                cost_to_expand TEXT NOT NULL,
                created_at REAL NOT NULL,
                expanded_at REAL,
                last_refreshed_at REAL NOT NULL,
                last_outcome TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE scenario_evidence (
                scenario_id TEXT NOT NULL,
                evidence_key TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                excerpt TEXT NOT NULL DEFAULT '',
                weight REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (scenario_id, evidence_key)
            )
            """
        )
        db.commit()
    store = ScenarioStore(db_path)
    asyncio.run(store.initialize())


def test_promote_sets_state_trusted_confidence_and_validated_at(tmp_path) -> None:
    async def _run() -> None:
        store = ScenarioStore(tmp_path / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(Scenario(id="s1", kind="change"))
        await store.promote_scenario("s1", new_state="trusted", confidence=0.8, evidence_hashes=["h1"], at=10.0)
        scenario = await store.get("s1")
        assert scenario is not None
        assert scenario.memory_state == "trusted"
        assert scenario.pinned == 1
        assert scenario.memory_last_validated_at == 10.0
        assert json.loads(scenario.memory_evidence_hashes_json) == ["h1"]

    asyncio.run(_run())


def test_demote_sets_state_and_bumps_contradiction_signal(tmp_path) -> None:
    async def _run() -> None:
        store = ScenarioStore(tmp_path / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(Scenario(id="s1", kind="change", memory_state="trusted", contradiction_signal=0.1))
        await store.demote_scenario("s1", new_state="demoted", contradiction_delta=0.25)
        scenario = await store.get("s1")
        assert scenario is not None
        assert scenario.memory_state == "demoted"
        assert scenario.contradiction_signal >= 0.35

    asyncio.run(_run())


def test_mark_absent_stale_preserves_active_and_pinned_scenarios(tmp_path) -> None:
    async def _run() -> None:
        store = ScenarioStore(tmp_path / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(Scenario(id="active", kind="change", freshness="fresh"))
        await store.upsert(Scenario(id="inactive", kind="change", freshness="fresh"))
        await store.upsert(Scenario(id="pinned", kind="change", freshness="fresh", pinned=1))

        await store.mark_absent_stale({"active"})

        active = await store.get("active")
        inactive = await store.get("inactive")
        pinned = await store.get("pinned")
        assert active is not None
        assert inactive is not None
        assert pinned is not None
        assert active.freshness == "fresh"
        assert inactive.freshness == "stale"
        assert pinned.freshness == "fresh"

    asyncio.run(_run())


def test_mark_stale_does_not_promote_explicitly_stale_scenarios(tmp_path) -> None:
    async def _run() -> None:
        store = ScenarioStore(tmp_path / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(Scenario(id="s1", kind="change", freshness="stale"))

        await store.mark_stale()

        scenario = await store.get("s1")
        assert scenario is not None
        assert scenario.freshness == "stale"

    asyncio.run(_run())


def test_list_top_hides_archived_scenarios_and_history_keeps_them(tmp_path) -> None:
    async def _run() -> None:
        store = ScenarioStore(tmp_path / ".vaner" / "scenarios.db")
        await store.initialize()
        old = time.time() - 7_200
        await store.upsert(
            Scenario(
                id="archived",
                kind="change",
                score=0.9,
                confidence=0.9,
                freshness="stale",
                created_at=old,
                last_refreshed_at=old,
                last_reinforced_at=old,
            )
        )
        await store.upsert(Scenario(id="live", kind="change", score=0.8, confidence=0.8, freshness="fresh"))

        live = await store.list_top(limit=10)
        history = await store.list_top(limit=10, visibility="history")

        assert [scenario.id for scenario in live] == ["live"]
        assert [scenario.id for scenario in history] == ["archived"]

    asyncio.run(_run())


def test_pinned_archived_scenario_remains_live_as_cooling(tmp_path) -> None:
    async def _run() -> None:
        store = ScenarioStore(tmp_path / ".vaner" / "scenarios.db")
        await store.initialize()
        old = time.time() - 7_200
        await store.upsert(
            Scenario(
                id="pinned",
                kind="change",
                score=0.5,
                confidence=0.5,
                freshness="stale",
                pinned=1,
                created_at=old,
                last_refreshed_at=old,
                last_reinforced_at=old,
            )
        )

        live = await store.list_top(limit=10)

        assert [scenario.id for scenario in live] == ["pinned"]
        assert live[0].visibility == "cooling"

    asyncio.run(_run())


def test_merge_memory_section_rejects_unknown_tag(tmp_path) -> None:
    async def _run() -> None:
        store = ScenarioStore(tmp_path / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(Scenario(id="s1", kind="change"))
        try:
            await store.merge_memory_section("s1", section="unknown", body="x", evidence_hashes=[])  # type: ignore[arg-type]
        except ValueError:
            return
        raise AssertionError("expected ValueError")

    asyncio.run(_run())
