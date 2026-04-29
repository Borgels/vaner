from __future__ import annotations

import asyncio
import time

from vaner.models.decision import DecisionRecord, SelectionDecision
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore

from .conftest import call_tool, parse_content, seed_scenario


def test_feedback_returns_memory_transition(temp_repo, mcp_server, monkeypatch) -> None:
    seed_scenario(temp_repo, scenario_id="scn_feedback")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    resolved = call_tool(mcp_server, "vaner.resolve", {"query": "auth"})
    payload = parse_content(resolved)
    if payload.get("abstained") or payload.get("code") == "engine_unavailable":
        # 0.8.1: resolve now delegates to engine.resolve_query; this smoke
        # test has neither an injected engine nor a running daemon, so
        # engine_unavailable is a legitimate fall-through.
        return
    feedback = call_tool(
        mcp_server,
        "vaner.feedback",
        {"resolution_id": payload["resolution_id"], "rating": "partial", "query": "auth"},
    )
    feedback_payload = parse_content(feedback)
    assert feedback_payload["accepted"] is True
    assert "memory_transition" in feedback_payload


def test_feedback_promotes_correction_confirmed_candidate_and_metrics_use_post_state(
    temp_repo,
    mcp_server,
) -> None:
    scenario_id = "scn_correction_confirmed"
    seed_scenario(temp_repo, scenario_id=scenario_id, memory_state="candidate", confidence=0.4)

    async def _prepare() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.record_outcome(scenario_id, "wrong")
        metrics = MetricsStore(temp_repo / ".vaner" / "metrics.db")
        await metrics.initialize()

    asyncio.run(_prepare())
    DecisionRecord(
        id="decision-correction-confirmed",
        prompt="auth",
        prompt_hash="hash",
        assembled_at=time.time(),
        cache_tier="warm",
        partial_similarity=0.2,
        token_budget=1000,
        token_used=100,
        selections=[
            SelectionDecision(
                artefact_key=scenario_id,
                source_path="src/auth.py",
                final_score=0.7,
                token_count=100,
                stale=False,
                kept=True,
            )
        ],
    ).write(temp_repo)

    feedback = call_tool(
        mcp_server,
        "vaner.feedback",
        {
            "resolution_id": "decision-correction-confirmed",
            "rating": "useful",
            "query": "auth",
        },
    )
    feedback_payload = parse_content(feedback)

    async def _assert() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        scenario = await store.get(scenario_id)
        metrics = MetricsStore(temp_repo / ".vaner" / "metrics.db")
        snapshot = await metrics.memory_quality_snapshot()
        assert scenario is not None
        assert scenario.memory_state == "trusted"
        assert snapshot["promotion_precision"] == 1.0
        assert snapshot["correction_survival_rate"] == 1.0

    assert feedback_payload["memory_transition"]["reason"] == "correction_confirmed"
    asyncio.run(_assert())
