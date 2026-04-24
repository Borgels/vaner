# SPDX-License-Identifier: Apache-2.0
"""WS1 — Deep-Run store + DAO tests (0.8.3).

Schema, lifecycle, single-active-session enforcement, expired-session
cleanup, and pass-log round-trip. Engine wiring is covered separately
by tests/test_engine/test_deep_run_lifecycle.py.
"""

from __future__ import annotations

import time

import pytest

from vaner.intent.deep_run import (
    DeepRunPassLogEntry,
    DeepRunSession,
)
from vaner.store import deep_run as deep_run_store
from vaner.store.artefacts import ArtefactStore

pytestmark = pytest.mark.asyncio


async def _initialized_store(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    return store


def _make_session(ends_in_seconds: float = 3600) -> DeepRunSession:
    return DeepRunSession.new(
        ends_at=time.time() + ends_in_seconds,
        preset="balanced",
        focus="active_goals",
        horizon_bias="balanced",
        locality="local_preferred",
        cost_cap_usd=0.0,
        workspace_root="/tmp/repo",
    )


# ---------------------------------------------------------------------------
# Session create / read
# ---------------------------------------------------------------------------


async def test_create_session_round_trip(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    session = _make_session()
    created = await deep_run_store.create_session(store.db_path, session)
    assert created.id == session.id

    fetched = await deep_run_store.get_active_session(store.db_path)
    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.preset == "balanced"
    assert fetched.cost_cap_usd == 0.0
    assert fetched.status == "active"
    assert fetched.metadata == {}


async def test_create_session_rejects_non_active_status(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    session = _make_session()
    session.status = "ended"
    with pytest.raises(ValueError, match="status='active'"):
        await deep_run_store.create_session(store.db_path, session)


async def test_create_session_persists_metadata_and_pause_reasons(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    session = DeepRunSession.new(
        ends_at=time.time() + 60,
        preset="aggressive",
        focus="current_workspace",
        horizon_bias="long_horizon",
        locality="local_only",
        cost_cap_usd=2.5,
        workspace_root="/tmp/repo",
        metadata={"caller": "cli", "tag": "overnight"},
    )
    session.pause_reasons = ["thermal"]
    await deep_run_store.create_session(store.db_path, session)
    fetched = await deep_run_store.get_session(store.db_path, session.id)
    assert fetched is not None
    assert fetched.metadata == {"caller": "cli", "tag": "overnight"}
    assert fetched.pause_reasons == ["thermal"]
    assert fetched.preset == "aggressive"
    assert fetched.locality == "local_only"
    assert fetched.cost_cap_usd == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# Single-active-session enforcement (the core WS1 invariant)
# ---------------------------------------------------------------------------


async def test_single_active_session_enforced(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    first = _make_session()
    await deep_run_store.create_session(store.db_path, first)

    second = _make_session()
    with pytest.raises(deep_run_store.DeepRunActiveSessionExistsError):
        await deep_run_store.create_session(store.db_path, second)

    # Original session is unchanged.
    fetched = await deep_run_store.get_active_session(store.db_path)
    assert fetched is not None
    assert fetched.id == first.id


async def test_can_start_session_after_previous_is_ended(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    first = _make_session()
    await deep_run_store.create_session(store.db_path, first)
    await deep_run_store.update_session_status(store.db_path, first.id, status="ended", ended_at=time.time())

    second = _make_session()
    await deep_run_store.create_session(store.db_path, second)
    fetched = await deep_run_store.get_active_session(store.db_path)
    assert fetched is not None
    assert fetched.id == second.id


# ---------------------------------------------------------------------------
# Status updates + active lookup semantics
# ---------------------------------------------------------------------------


async def test_paused_session_is_returned_by_get_active(tmp_path) -> None:
    """``get_active_session`` returns paused sessions too — the engine
    resumes them when pause reasons clear, so they're still in flight."""
    store = await _initialized_store(tmp_path)
    session = _make_session()
    await deep_run_store.create_session(store.db_path, session)
    await deep_run_store.update_session_status(
        store.db_path,
        session.id,
        status="paused",
        pause_reasons=["thermal", "battery"],
    )
    fetched = await deep_run_store.get_active_session(store.db_path)
    assert fetched is not None
    assert fetched.status == "paused"
    assert fetched.pause_reasons == ["thermal", "battery"]


async def test_ended_session_not_returned_by_get_active(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    session = _make_session()
    await deep_run_store.create_session(store.db_path, session)
    await deep_run_store.update_session_status(
        store.db_path,
        session.id,
        status="killed",
        ended_at=time.time(),
        cancelled_reason="user_kill",
    )
    assert await deep_run_store.get_active_session(store.db_path) is None
    fetched = await deep_run_store.get_session(store.db_path, session.id)
    assert fetched is not None
    assert fetched.status == "killed"
    assert fetched.cancelled_reason == "user_kill"


# ---------------------------------------------------------------------------
# Counter increments
# ---------------------------------------------------------------------------


async def test_increment_session_counters_atomic_add(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    session = _make_session()
    await deep_run_store.create_session(store.db_path, session)
    await deep_run_store.increment_session_counters(
        store.db_path,
        session.id,
        cycles_run=1,
        matured_kept=2,
        matured_discarded=1,
        spend_usd=0.05,
    )
    await deep_run_store.increment_session_counters(
        store.db_path,
        session.id,
        cycles_run=1,
        matured_kept=3,
        matured_failed=1,
        spend_usd=0.10,
    )
    fetched = await deep_run_store.get_session(store.db_path, session.id)
    assert fetched is not None
    assert fetched.cycles_run == 2
    assert fetched.matured_kept == 5
    assert fetched.matured_discarded == 1
    assert fetched.matured_failed == 1
    assert fetched.spend_usd == pytest.approx(0.15)


async def test_increment_with_all_zero_is_noop(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    session = _make_session()
    await deep_run_store.create_session(store.db_path, session)
    changed = await deep_run_store.increment_session_counters(store.db_path, session.id)
    assert changed is False


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


async def test_list_sessions_newest_first_with_status_filter(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    older = _make_session()
    older.started_at = time.time() - 120
    await deep_run_store.create_session(store.db_path, older)
    await deep_run_store.update_session_status(store.db_path, older.id, status="ended", ended_at=time.time() - 60)
    newer = _make_session()
    newer.started_at = time.time()
    await deep_run_store.create_session(store.db_path, newer)

    all_sessions = await deep_run_store.list_sessions(store.db_path, limit=10)
    assert [s.id for s in all_sessions] == [newer.id, older.id]

    only_active = await deep_run_store.list_sessions(store.db_path, status="active")
    assert [s.id for s in only_active] == [newer.id]

    only_ended = await deep_run_store.list_sessions(store.db_path, status="ended")
    assert [s.id for s in only_ended] == [older.id]


# ---------------------------------------------------------------------------
# Expiry / restart cleanup
# ---------------------------------------------------------------------------


async def test_close_expired_sessions_closes_past_ends_at(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    expired = _make_session(ends_in_seconds=-10)  # ends_at in the past
    await deep_run_store.create_session(store.db_path, expired)

    closed_count = await deep_run_store.close_expired_sessions(store.db_path, now=time.time())
    assert closed_count == 1
    fetched = await deep_run_store.get_session(store.db_path, expired.id)
    assert fetched is not None
    assert fetched.status == "ended"
    assert fetched.cancelled_reason == "expired_on_restart"
    assert fetched.ended_at is not None


async def test_close_expired_sessions_leaves_future_sessions_alone(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    in_flight = _make_session(ends_in_seconds=3600)
    await deep_run_store.create_session(store.db_path, in_flight)
    closed_count = await deep_run_store.close_expired_sessions(store.db_path, now=time.time())
    assert closed_count == 0
    fetched = await deep_run_store.get_active_session(store.db_path)
    assert fetched is not None
    assert fetched.id == in_flight.id


async def test_close_expired_sessions_handles_paused_too(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    paused_expired = _make_session(ends_in_seconds=-10)
    await deep_run_store.create_session(store.db_path, paused_expired)
    await deep_run_store.update_session_status(
        store.db_path,
        paused_expired.id,
        status="paused",
        pause_reasons=["battery"],
    )
    closed_count = await deep_run_store.close_expired_sessions(store.db_path, now=time.time())
    assert closed_count == 1


# ---------------------------------------------------------------------------
# Pass-log round-trip
# ---------------------------------------------------------------------------


async def test_pass_log_round_trip_with_filters(tmp_path) -> None:
    store = await _initialized_store(tmp_path)
    session = _make_session()
    await deep_run_store.create_session(store.db_path, session)
    a = DeepRunPassLogEntry.new(
        session_id=session.id,
        prediction_id="pred-A",
        action="matured_kept",
        cycle_index=1,
        before_evidence_score=0.4,
        after_evidence_score=0.7,
    )
    b = DeepRunPassLogEntry.new(
        session_id=session.id,
        prediction_id="pred-A",
        action="matured_discarded",
        cycle_index=2,
    )
    c = DeepRunPassLogEntry.new(
        session_id=session.id,
        prediction_id="pred-B",
        action="explored",
        cycle_index=2,
    )
    for entry in (a, b, c):
        await deep_run_store.insert_pass_log_entry(store.db_path, entry)

    all_entries = await deep_run_store.list_pass_log_entries(store.db_path, session_id=session.id)
    assert {e.id for e in all_entries} == {a.id, b.id, c.id}

    by_prediction = await deep_run_store.list_pass_log_entries(store.db_path, session_id=session.id, prediction_id="pred-A")
    assert {e.id for e in by_prediction} == {a.id, b.id}

    only_kept = await deep_run_store.list_pass_log_entries(store.db_path, session_id=session.id, action="matured_kept")
    assert [e.id for e in only_kept] == [a.id]
    assert only_kept[0].before_evidence_score == pytest.approx(0.4)
    assert only_kept[0].after_evidence_score == pytest.approx(0.7)
