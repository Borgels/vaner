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
    # Volatility gate requires >= 0.2 before marking stale; temp_repo has no git so patch it.
    monkeypatch.setattr("vaner.mcp.server.semantic_volatility", lambda _paths: 0.5)
    _ = parse_content(call_tool(mcp_server, "vaner.resolve", {"query": "auth flow"}))

    async def _assert_stale() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        updated = await store.get("scn_drift")
        assert updated is not None
        assert updated.memory_state == "stale"

    asyncio.run(_assert_stale())


def test_resolve_default_omits_briefing(temp_repo, mcp_server, monkeypatch) -> None:
    """Legacy callers must not see the new fields populated."""
    seed_scenario(temp_repo, scenario_id="scn_briefing_default")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    monkeypatch.setattr("vaner.mcp.server.decide_reuse", lambda _inp: "reuse_payload")
    payload = parse_content(call_tool(mcp_server, "vaner.resolve", {"query": "auth flow"}))
    # Not abstained — we want the Resolution-shaped fields
    assert payload.get("abstained") is not True
    assert payload.get("prepared_briefing") is None
    assert payload.get("predicted_response") is None
    assert payload.get("briefing_token_used", 0) == 0
    assert payload.get("briefing_token_budget", 0) == 0


def test_resolve_include_briefing_returns_full_prepared_context(temp_repo, mcp_server, monkeypatch) -> None:
    """Opting in surfaces the full briefing that was previously truncated to 400 chars."""
    seed_scenario(temp_repo, scenario_id="scn_briefing_opt_in")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    monkeypatch.setattr("vaner.mcp.server.decide_reuse", lambda _inp: "reuse_payload")
    payload = parse_content(call_tool(mcp_server, "vaner.resolve", {"query": "auth flow", "include_briefing": True}))
    assert payload.get("abstained") is not True
    briefing = payload.get("prepared_briefing")
    assert isinstance(briefing, str) and briefing
    # The briefing must be the full content — not the 400-char summary
    assert payload["prepared_briefing"].startswith(payload["summary"].rstrip(".")[:20])
    assert payload.get("briefing_token_used", 0) > 0
    assert payload.get("briefing_token_budget", 0) > 0


def test_resolve_include_predicted_response_returns_none_when_uncached(temp_repo, mcp_server, monkeypatch) -> None:
    """Without a cached draft, predicted_response stays None even when opted in."""
    seed_scenario(temp_repo, scenario_id="scn_predicted_response")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    monkeypatch.setattr("vaner.mcp.server.decide_reuse", lambda _inp: "reuse_payload")
    payload = parse_content(
        call_tool(
            mcp_server,
            "vaner.resolve",
            {"query": "auth flow", "include_predicted_response": True},
        )
    )
    assert payload.get("abstained") is not True
    assert payload.get("predicted_response") is None


def test_resolve_default_omits_metrics(temp_repo, mcp_server, monkeypatch) -> None:
    """Metrics surface is opt-in; default response carries no metrics block."""
    seed_scenario(temp_repo, scenario_id="scn_metrics_default")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    monkeypatch.setattr("vaner.mcp.server.decide_reuse", lambda _inp: "reuse_payload")
    payload = parse_content(call_tool(mcp_server, "vaner.resolve", {"query": "auth flow"}))
    assert payload.get("abstained") is not True
    assert payload.get("metrics") is None


def test_resolve_include_metrics_returns_runtime_economics(temp_repo, mcp_server, monkeypatch) -> None:
    """Opt-in metrics surface runtime tokens, tier, latency, cost."""
    seed_scenario(temp_repo, scenario_id="scn_metrics_opt_in")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    monkeypatch.setattr("vaner.mcp.server.decide_reuse", lambda _inp: "reuse_payload")
    payload = parse_content(
        call_tool(
            mcp_server,
            "vaner.resolve",
            {
                "query": "auth flow",
                "include_briefing": True,
                "include_metrics": True,
                "estimated_cost_per_1k_tokens": 2.50,  # e.g. gpt-4o input price
            },
        )
    )
    assert payload.get("abstained") is not True
    metrics = payload.get("metrics")
    assert isinstance(metrics, dict)
    assert metrics["briefing_tokens"] > 0  # prepared_briefing was populated
    assert metrics["total_context_tokens"] >= metrics["briefing_tokens"]
    assert metrics["cache_tier"] in {"miss", "warm_start", "partial_hit", "full_hit"}
    assert metrics["freshness"] in {"fresh", "recent", "stale"}
    assert metrics["elapsed_ms"] >= 0.0
    # Cost estimate respects the caller-provided rate
    assert metrics["estimated_cost_per_1k_tokens"] == 2.50
    expected_cost = (metrics["total_context_tokens"] / 1000.0) * 2.50
    assert abs(metrics["estimated_cost_usd"] - expected_cost) < 1e-6
