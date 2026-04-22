# SPDX-License-Identifier: Apache-2.0
"""Tests for the opt-in predicted-response precompute pathway.

Verifies that:
  1. The pathway is gated off by default.
  2. When enabled, Vaner generates a draft for validated prompt macros and
     caches it with the ``predicted_response`` enrichment key.
  3. Unvalidated macros (``use_count < min_use_count``) are ignored.
  4. ``max_per_cycle`` caps the number of drafts actually generated.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter


def _make_engine(repo_root: Path, *, llm) -> VanerEngine:
    adapter = CodeRepoAdapter(repo_root)
    engine = VanerEngine(adapter=adapter, llm=llm)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "none"  # don't interfere with a focused test
    return engine


@pytest.mark.asyncio
async def test_predicted_response_disabled_by_default(temp_repo: Path):
    calls = {"n": 0}

    async def _llm(_prompt: str) -> str:
        calls["n"] += 1
        return "draft"

    engine = _make_engine(temp_repo, llm=_llm)
    await engine.initialize()
    await engine.store.bump_prompt_macro(
        macro_key="review latest implementation",
        example_query="do a code review of the latest implementation",
        category="review",
        confidence=0.9,
    )
    # Hit the threshold so the macro would otherwise qualify.
    for _ in range(4):
        await engine.store.bump_prompt_macro(
            macro_key="review latest implementation",
            example_query="do a code review of the latest implementation",
            category="review",
            confidence=0.9,
        )
    assert engine.config.exploration.predicted_response_enabled is False

    count = await engine._precompute_predicted_responses(
        max_per_cycle=0,  # mimic the disabled-flag gate from precompute_cycle
        min_use_count=3,
        deadline=None,
        available_paths=["sample.py"],
        recent_queries=["how does this work?"],
    )
    assert count == 0
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_predicted_response_generates_draft_for_validated_macro(temp_repo: Path):
    async def _llm(_prompt: str) -> str:
        return "Draft code review: function `hello` looks trivially correct."

    engine = _make_engine(temp_repo, llm=_llm)
    engine.config.exploration.predicted_response_enabled = True
    engine.config.exploration.predicted_response_max_per_cycle = 1
    engine.config.exploration.predicted_response_min_macro_use_count = 3
    await engine.prepare()
    for _ in range(5):
        await engine.store.bump_prompt_macro(
            macro_key="review latest implementation",
            example_query="do a code review of the latest implementation",
            category="review",
            confidence=0.9,
        )

    generated = await engine._precompute_predicted_responses(
        max_per_cycle=1,
        min_use_count=3,
        deadline=None,
        available_paths=["sample.py"],
        recent_queries=["what does hello() return?"],
    )
    assert generated == 1
    cache_rows = await engine.store.list_prediction_cache()
    draft_rows = [row for row in cache_rows if "predicted_response" in (row.get("enrichment") or {})]
    assert len(draft_rows) == 1
    assert "Draft code review" in str(draft_rows[0]["enrichment"].get("predicted_response"))
    assert draft_rows[0]["enrichment"].get("predicted_response_macro") == "review latest implementation"


@pytest.mark.asyncio
async def test_predicted_response_skips_unvalidated_macros(temp_repo: Path):
    async def _llm(_prompt: str) -> str:
        pytest.fail("LLM should not be called for unvalidated macros")
        return ""

    engine = _make_engine(temp_repo, llm=_llm)
    engine.config.exploration.predicted_response_enabled = True
    engine.config.exploration.predicted_response_max_per_cycle = 3
    engine.config.exploration.predicted_response_min_macro_use_count = 5
    await engine.initialize()
    # Only record the macro twice — well below the threshold of 5.
    for _ in range(2):
        await engine.store.bump_prompt_macro(
            macro_key="explain arcs",
            example_query="explain what this module does",
            category="understanding",
            confidence=0.4,
        )

    generated = await engine._precompute_predicted_responses(
        max_per_cycle=3,
        min_use_count=5,
        deadline=None,
        available_paths=["sample.py"],
        recent_queries=[],
    )
    assert generated == 0


@pytest.mark.asyncio
async def test_predicted_response_respects_max_per_cycle(temp_repo: Path):
    call_count = {"n": 0}

    async def _llm(_prompt: str) -> str:
        call_count["n"] += 1
        return f"draft-{call_count['n']}"

    engine = _make_engine(temp_repo, llm=_llm)
    engine.config.exploration.predicted_response_enabled = True
    engine.config.exploration.predicted_response_max_per_cycle = 1
    engine.config.exploration.predicted_response_min_macro_use_count = 3
    await engine.prepare()
    for key in ("one", "two", "three"):
        for _ in range(4):
            await engine.store.bump_prompt_macro(
                macro_key=key,
                example_query=f"prompt {key}",
                category="understanding",
                confidence=0.7,
            )

    generated = await engine._precompute_predicted_responses(
        max_per_cycle=1,
        min_use_count=3,
        deadline=None,
        available_paths=["sample.py"],
        recent_queries=[],
    )
    assert generated == 1
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_predicted_response_respects_deadline(temp_repo: Path):
    async def _llm(_prompt: str) -> str:  # pragma: no cover - should not run
        pytest.fail("LLM should not be called after the deadline")
        return ""

    engine = _make_engine(temp_repo, llm=_llm)
    engine.config.exploration.predicted_response_enabled = True
    engine.config.exploration.predicted_response_max_per_cycle = 5
    engine.config.exploration.predicted_response_min_macro_use_count = 1
    await engine.prepare()
    await engine.store.bump_prompt_macro(
        macro_key="k",
        example_query="q",
        category="review",
        confidence=0.9,
    )
    # Past deadline — method must bail immediately.
    past_deadline = time.monotonic() - 5.0
    generated = await engine._precompute_predicted_responses(
        max_per_cycle=5,
        min_use_count=1,
        deadline=past_deadline,
        available_paths=["sample.py"],
        recent_queries=[],
    )
    assert generated == 0
