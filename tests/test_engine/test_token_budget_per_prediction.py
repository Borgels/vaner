# SPDX-License-Identifier: Apache-2.0
"""WS2 — per-prediction token budget + thinking-trace capture.

Verifies that _explore_scenario_with_llm:
  1. Calls the structured LLM when one is provided.
  2. Passes max_tokens derived from the parent prediction's remaining budget.
  3. Records the thinking preamble via registry.attach_artifact(thinking=...).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vaner.clients.llm_response import LLMResponse
from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter


def _seed_repo(repo: Path) -> None:
    for sub in ("src", "tests", "docs", "debug", "review"):
        (repo / sub).mkdir(exist_ok=True)
    (repo / "src" / "parser.py").write_text("def parse(x):\n    return x\n")
    (repo / "src" / "handler.py").write_text("def handle(x):\n    return x\n")
    (repo / "src" / "engine.py").write_text("def run():\n    pass\n")
    (repo / "tests" / "test_parser.py").write_text("def test_parse():\n    pass\n")
    (repo / "tests" / "conftest.py").write_text("")
    (repo / "docs" / "README.md").write_text("# API\n")
    (repo / "debug" / "trace.py").write_text("def trace():\n    pass\n")
    (repo / "review" / "audit.py").write_text("def audit():\n    pass\n")


@pytest.mark.asyncio
async def test_structured_llm_is_called_and_max_tokens_is_clamped(temp_repo: Path):
    _seed_repo(temp_repo)
    calls: list[dict] = []

    async def _stub_bare_llm(prompt: str) -> str:
        # Must exist because the engine checks callable(self.llm) for the
        # "should_use_llm" path, even when structured_llm overrides.
        return '{"ranked_files": ["src/parser.py"], "semantic_intent": "probe", "confidence": 0.7, "follow_on": []}'

    async def _stub_structured(prompt: str, *, max_tokens: int | None = None) -> LLMResponse:
        calls.append({"prompt_len": len(prompt), "max_tokens": max_tokens})
        return LLMResponse(
            thinking="Let me reason about parser internals.",
            content='{"ranked_files": ["src/parser.py"], "semantic_intent": "probe parser", "confidence": 0.7, "follow_on": []}',
            raw="",
        )

    adapter = CodeRepoAdapter(temp_repo)
    engine = VanerEngine(adapter=adapter, llm=_stub_bare_llm, structured_llm=_stub_structured)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "all"
    # Keep thinking budget modest so the clamp is observable.
    engine.config.backend.max_response_tokens = 512
    engine.config.backend.reasoning_token_budget = 1024
    engine.config.backend.reasoning_mode = "allowed"
    await engine.initialize()
    for q in ["implement parser", "add tests for parser"]:
        engine._arc_model.observe(q)
        await engine.store.insert_query_history(
            session_id="s",
            query_text=q,
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
        )
    await engine.precompute_cycle()

    # The structured client must have been called at least once.
    assert calls, "structured_llm was never invoked"
    # All invocations should carry a bounded max_tokens derived from the
    # prediction's remaining budget + the config cap. Upper bound = 512 + 1024.
    ceiling = 512 + 1024
    for call in calls:
        if call["max_tokens"] is not None:
            assert 0 < call["max_tokens"] <= ceiling


@pytest.mark.asyncio
async def test_thinking_trace_is_captured_on_parent_prediction(temp_repo: Path):
    _seed_repo(temp_repo)

    async def _stub_bare_llm(prompt: str) -> str:
        return '{"ranked_files": [], "semantic_intent": "", "confidence": 0.0, "follow_on": []}'

    async def _stub_structured(prompt: str, *, max_tokens: int | None = None) -> LLMResponse:
        return LLMResponse(
            thinking="I considered option A versus option B.",
            content='{"ranked_files": ["src/parser.py"], "semantic_intent": "probe", "confidence": 0.7, "follow_on": []}',
            raw="",
        )

    adapter = CodeRepoAdapter(temp_repo)
    engine = VanerEngine(adapter=adapter, llm=_stub_bare_llm, structured_llm=_stub_structured)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "all"
    await engine.initialize()
    for q in ["implement parser", "add tests for parser"]:
        engine._arc_model.observe(q)
        await engine.store.insert_query_history(
            session_id="s",
            query_text=q,
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
        )
    await engine.precompute_cycle()

    # At least one prediction should have captured the thinking trace.
    active = engine.get_active_predictions()
    traces = [p.artifacts.thinking_traces for p in active if p.artifacts.thinking_traces]
    assert traces, "no prediction captured a thinking trace — registry.attach_artifact(thinking=…) not wired"
    for trace_list in traces:
        assert any("option A" in t for t in trace_list)


@pytest.mark.asyncio
async def test_legacy_bare_string_llm_path_still_works(temp_repo: Path):
    """Back-compat: callers that don't supply structured_llm still get
    correct behaviour via the legacy `self.llm` string callable."""
    _seed_repo(temp_repo)

    async def _stub_bare_llm(prompt: str) -> str:
        return '{"ranked_files": ["src/parser.py"], "semantic_intent": "probe", "confidence": 0.7, "follow_on": []}'

    adapter = CodeRepoAdapter(temp_repo)
    engine = VanerEngine(adapter=adapter, llm=_stub_bare_llm)  # no structured_llm
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "all"
    await engine.initialize()
    for q in ["implement parser", "add tests for parser"]:
        engine._arc_model.observe(q)
        await engine.store.insert_query_history(
            session_id="s",
            query_text=q,
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
        )
    await engine.precompute_cycle()

    active = engine.get_active_predictions()
    touched = [p for p in active if p.artifacts.scenario_ids and p.run.tokens_used > 0]
    assert touched, "bare-string LLM path should still reach predictions"
    # No structured client means no thinking traces.
    for p in active:
        assert p.artifacts.thinking_traces == []
