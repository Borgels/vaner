from __future__ import annotations

import asyncio
import json
import sqlite3

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
