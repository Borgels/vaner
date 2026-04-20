from __future__ import annotations

import asyncio

from vaner.mcp.contracts import ConflictSignal
from vaner.store.scenarios import ScenarioStore

from .conftest import call_tool, parse_content, seed_scenario


def test_resolve_returns_resolution_or_abstain(temp_repo, mcp_server, monkeypatch) -> None:
    seed_scenario(temp_repo, scenario_id="scn_resolve")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    result = call_tool(mcp_server, "vaner.resolve", {"query": "where auth is enforced"})
    payload = parse_content(result)
    assert ("resolution_id" in payload) or payload.get("abstained") is True


def test_resolve_abstains_on_strong_memory_conflict(temp_repo, mcp_server, monkeypatch) -> None:
    seed_scenario(temp_repo, scenario_id="scn_conflict")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    monkeypatch.setattr(
        "vaner.mcp.server.detect_conflict",
        lambda _inp: ConflictSignal(has_conflict=True, strength=0.85, reason="forced-test-conflict"),
    )
    result = call_tool(mcp_server, "vaner.resolve", {"query": "where auth is enforced"})
    payload = parse_content(result)
    assert payload["abstained"] is True
    assert payload["reason"] == "memory_conflict"


def test_resolve_reuse_tiers_affect_provenance_mode(temp_repo, mcp_server, monkeypatch) -> None:
    seed_scenario(temp_repo, scenario_id="scn_reuse")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))

    monkeypatch.setattr("vaner.mcp.server.decide_reuse", lambda _inp: "reuse_payload")
    payload_reuse = parse_content(call_tool(mcp_server, "vaner.resolve", {"query": "auth flow"}))
    assert payload_reuse["provenance"]["mode"] == "predictive_hit"

    monkeypatch.setattr("vaner.mcp.server.decide_reuse", lambda _inp: "rerank_prior")
    payload_rerank = parse_content(call_tool(mcp_server, "vaner.resolve", {"query": "auth flow"}))
    assert payload_rerank["provenance"]["mode"] == "cached_result"

    monkeypatch.setattr("vaner.mcp.server.decide_reuse", lambda _inp: "ignore_prior")
    payload_fresh = parse_content(call_tool(mcp_server, "vaner.resolve", {"query": "auth flow"}))
    assert payload_fresh["provenance"]["mode"] in {"fresh_resolution", "retrieval_fallback"}


def test_resolve_adds_memory_conflict_gap_when_conflict_is_moderate(temp_repo, mcp_server, monkeypatch) -> None:
    seed_scenario(temp_repo, scenario_id="scn_conflict_gap")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    monkeypatch.setattr(
        "vaner.mcp.server.detect_conflict",
        lambda _inp: ConflictSignal(has_conflict=True, strength=0.55, reason="forced-test-conflict"),
    )
    payload = parse_content(call_tool(mcp_server, "vaner.resolve", {"query": "auth flow"}))
    assert "memory_conflict" in payload.get("gaps", [])


def test_resolve_marks_trusted_stale_on_persisted_fingerprint_drift(temp_repo, mcp_server, monkeypatch) -> None:
    seed_scenario(temp_repo, scenario_id="scn_drift", memory_state="trusted")

    async def _prepare() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        scenario = await store.get("scn_drift")
        assert scenario is not None
        scenario.memory_evidence_hashes_json = '["fp_old"]'
        await store.upsert(scenario)

    asyncio.run(_prepare())
    monkeypatch.setattr("vaner.mcp.server._scenario_fingerprints", lambda _scenario: ["fp_new"])
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    _ = parse_content(call_tool(mcp_server, "vaner.resolve", {"query": "auth flow"}))

    async def _assert_stale() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        updated = await store.get("scn_drift")
        assert updated is not None
        assert updated.memory_state == "stale"

    asyncio.run(_assert_stale())
