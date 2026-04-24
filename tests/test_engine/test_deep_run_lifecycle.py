# SPDX-License-Identifier: Apache-2.0
"""WS1 — Deep-Run engine API tests (0.8.3).

Engine integration: ``start_deep_run`` / ``stop_deep_run`` /
``current_deep_run`` / ``list_deep_run_sessions`` plus restart-safety
(resume vs. expire) and single-active error surfacing.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.intent.deep_run import DeepRunSession
from vaner.store import deep_run as deep_run_store

pytestmark = pytest.mark.asyncio


async def _stub_llm(_prompt: str) -> str:
    return '{"ranked_files": [], "semantic_intent": "", "confidence": 0.0, "follow_on": []}'


def _seed_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "sample.py").write_text("def hello():\n    return 'hi'\n")


def _make_engine(repo_root: Path) -> VanerEngine:
    _seed_repo(repo_root)
    engine = VanerEngine(adapter=CodeRepoAdapter(repo_root), llm=_stub_llm)
    engine.config.compute.idle_only = False
    return engine


# ---------------------------------------------------------------------------
# Lifecycle: start / current / stop
# ---------------------------------------------------------------------------


async def test_start_then_current_returns_same_session(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    session = await engine.start_deep_run(
        ends_at=time.time() + 3600,
        preset="balanced",
    )
    assert session.status == "active"
    assert session.preset == "balanced"
    cached = await engine.current_deep_run()
    assert cached is not None
    assert cached.id == session.id


async def test_start_records_workspace_root_default(tmp_path) -> None:
    repo = tmp_path / "repo"
    engine = _make_engine(repo)
    session = await engine.start_deep_run(ends_at=time.time() + 3600)
    assert session.workspace_root == str(repo)


async def test_start_then_stop_returns_summary_and_clears_cache(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 3600)
    summary = await engine.stop_deep_run()
    assert summary is not None
    assert summary.final_status == "ended"
    assert summary.cycles_run == 0
    assert summary.matured_kept == 0
    assert await engine.current_deep_run() is None


async def test_stop_with_kill_records_killed_status(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 3600)
    summary = await engine.stop_deep_run(kill=True, reason="user_kill")
    assert summary is not None
    assert summary.final_status == "killed"
    # The persisted row also carries the cancelled_reason for the
    # audit log.
    sessions = await engine.list_deep_run_sessions(limit=5)
    assert len(sessions) == 1
    assert sessions[0].cancelled_reason == "user_kill"


async def test_stop_without_active_session_returns_none(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    summary = await engine.stop_deep_run()
    assert summary is None


# ---------------------------------------------------------------------------
# Single-active enforcement (the core WS1 invariant from the engine API)
# ---------------------------------------------------------------------------


async def test_start_twice_raises(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 3600)
    with pytest.raises(deep_run_store.DeepRunActiveSessionExistsError):
        await engine.start_deep_run(ends_at=time.time() + 3600)


async def test_can_restart_after_clean_stop(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    first = await engine.start_deep_run(ends_at=time.time() + 60)
    await engine.stop_deep_run()
    second = await engine.start_deep_run(ends_at=time.time() + 60)
    assert first.id != second.id
    cached = await engine.current_deep_run()
    assert cached is not None
    assert cached.id == second.id


# ---------------------------------------------------------------------------
# list_deep_run_sessions
# ---------------------------------------------------------------------------


async def test_list_includes_terminated_sessions_newest_first(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    first = await engine.start_deep_run(ends_at=time.time() + 60)
    await engine.stop_deep_run()
    second = await engine.start_deep_run(ends_at=time.time() + 60)
    sessions = await engine.list_deep_run_sessions(limit=10)
    ids = [s.id for s in sessions]
    assert ids[0] == second.id
    assert first.id in ids


# ---------------------------------------------------------------------------
# Restart safety: pre-existing active session in DB is restored;
# expired sessions are auto-closed.
# ---------------------------------------------------------------------------


async def test_resume_restores_in_flight_session(tmp_path) -> None:
    repo = tmp_path / "repo"
    engine_a = _make_engine(repo)
    started = await engine_a.start_deep_run(ends_at=time.time() + 3600)

    # Simulate a daemon restart: a fresh engine over the same repo /
    # store should pick the active session straight back up.
    engine_b = _make_engine(repo)
    cached = await engine_b.current_deep_run()
    assert cached is not None
    assert cached.id == started.id
    assert cached.status == "active"


async def test_resume_expires_past_ends_at_session(tmp_path) -> None:
    repo = tmp_path / "repo"
    engine_a = _make_engine(repo)
    # Construct a session whose ends_at is already past, persist it
    # directly, then simulate a fresh engine restart. The resume hook
    # should auto-close it with cancelled_reason="expired_on_restart".
    await engine_a.initialize()
    expired = DeepRunSession.new(
        ends_at=time.time() - 30,
        preset="balanced",
        focus="active_goals",
        horizon_bias="balanced",
        locality="local_preferred",
        cost_cap_usd=0.0,
        workspace_root=str(repo),
    )
    await deep_run_store.create_session(engine_a.store.db_path, expired)

    engine_b = _make_engine(repo)
    cached = await engine_b.current_deep_run()
    assert cached is None
    fetched = await deep_run_store.get_session(engine_b.store.db_path, expired.id)
    assert fetched is not None
    assert fetched.status == "ended"
    assert fetched.cancelled_reason == "expired_on_restart"
