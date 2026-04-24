# SPDX-License-Identifier: Apache-2.0
"""Tests for spaced-timestamp history injection.

The default ``inject_history(list[str])`` path stamps every injected query
with ``time.time()`` at insertion, which collapses a real developer session
into a timing burst. A benchmark that wants to test Vaner's adaptive
cycle budget must be able to replay spaced history so the
``ActivityTimingModel`` sees realistic inter-prompt gaps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter


def _make_engine(repo_root: Path) -> VanerEngine:
    adapter = CodeRepoAdapter(repo_root)
    engine = VanerEngine(adapter=adapter, llm=None)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "none"
    return engine


@pytest.mark.asyncio
async def test_string_list_history_still_works(temp_repo: Path) -> None:
    engine = _make_engine(temp_repo)
    injected = await engine.inject_history(
        ["plan the feature", "implement the handler", "write tests"],
        session_id="legacy",
    )
    assert injected == 3


@pytest.mark.asyncio
async def test_timestamped_history_updates_timing_ema(temp_repo: Path) -> None:
    engine = _make_engine(temp_repo)
    base = 1_700_000_000.0
    timed = [
        ("plan the feature", base),
        ("implement the handler", base + 60.0),
        ("write the first test", base + 120.0),
        ("fix the failing assertion", base + 180.0),
        ("review the patch", base + 240.0),
    ]
    injected = await engine.inject_history(timed, session_id="spaced")
    assert injected == 5

    model = engine._timing_model
    assert model._last_prompt_ts == pytest.approx(base + 240.0)
    # Gaps are all 60s — EMA should reflect that, not a compressed burst.
    assert model._ema == pytest.approx(60.0, abs=1e-6)
    assert model._sample_count == 4


@pytest.mark.asyncio
async def test_timestamped_history_persists_to_query_history(temp_repo: Path) -> None:
    engine = _make_engine(temp_repo)
    base = 1_700_000_000.0
    await engine.inject_history(
        [("first prompt", base), ("second prompt", base + 300.0)],
        session_id="persist",
    )
    rows = await engine.store.list_query_history(limit=10)
    timestamps = sorted(float(r["timestamp"]) for r in rows)
    assert timestamps == [pytest.approx(base), pytest.approx(base + 300.0)]


@pytest.mark.asyncio
async def test_session_boundary_gap_is_excluded_from_ema(temp_repo: Path) -> None:
    """Gaps longer than the session boundary (default 180s) must not pollute the EMA."""
    engine = _make_engine(temp_repo)
    base = 1_700_000_000.0
    timed = [
        ("first prompt", base),
        ("follow-up", base + 20.0),
        # 1 hour idle — this exceeds active_session_gap_seconds (180s)
        ("next session", base + 3620.0),
        ("next session follow-up", base + 3640.0),
    ]
    await engine.inject_history(timed, session_id="boundary")
    model = engine._timing_model
    # Two 20s gaps counted; the 1-hour gap is treated as a session boundary.
    assert model._ema == pytest.approx(20.0, abs=1e-6)
    assert model._sample_count == 2
