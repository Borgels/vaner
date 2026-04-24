# SPDX-License-Identifier: Apache-2.0
"""WS1 — Deep-Run types tests (0.8.3).

Type-level invariants only. Store-layer + engine-layer behavior is
covered by tests/test_store/test_deep_run.py and
tests/test_engine/test_deep_run_lifecycle.py.
"""

from __future__ import annotations

import time

import pytest

from vaner.intent.deep_run import (
    DeepRunPassLogEntry,
    DeepRunSession,
    DeepRunSummary,
    new_deep_run_pass_id,
    new_deep_run_session_id,
)


def test_session_new_defaults_to_active() -> None:
    session = DeepRunSession.new(
        ends_at=time.time() + 3600,
        preset="balanced",
        focus="active_goals",
        horizon_bias="balanced",
        locality="local_preferred",
        cost_cap_usd=0.0,
        workspace_root="/tmp/repo",
    )
    assert session.status == "active"
    assert session.cycles_run == 0
    assert session.spend_usd == 0.0
    assert session.matured_total == 0
    assert not session.is_terminal
    assert session.metadata == {}


def test_session_metadata_is_copied_not_aliased() -> None:
    """Caller-supplied metadata dict must not bleed back through the
    session — the field is its own dict so mutations stay local."""
    payload = {"caller": "cli", "tag": "overnight"}
    session = DeepRunSession.new(
        ends_at=time.time() + 3600,
        preset="aggressive",
        focus="active_goals",
        horizon_bias="long_horizon",
        locality="local_only",
        cost_cap_usd=0.0,
        workspace_root="/tmp/repo",
        metadata=payload,
    )
    payload["tag"] = "MUTATED"
    assert session.metadata == {"caller": "cli", "tag": "overnight"}


@pytest.mark.parametrize(
    "status, expected",
    [
        ("active", False),
        ("paused", False),
        ("ended", True),
        ("killed", True),
    ],
)
def test_is_terminal_matches_status(status: str, expected: bool) -> None:
    session = DeepRunSession.new(
        ends_at=time.time() + 60,
        preset="balanced",
        focus="active_goals",
        horizon_bias="balanced",
        locality="local_preferred",
        cost_cap_usd=0.0,
        workspace_root="/tmp/repo",
    )
    session.status = status  # type: ignore[assignment]
    assert session.is_terminal is expected


def test_matured_total_sums_the_four_counters() -> None:
    """The honesty discipline (§9.2) demands four separate counters be
    surfaced. ``matured_total`` is provided for arithmetic, not to
    flatten reporting."""
    session = DeepRunSession.new(
        ends_at=time.time() + 60,
        preset="balanced",
        focus="active_goals",
        horizon_bias="balanced",
        locality="local_preferred",
        cost_cap_usd=0.0,
        workspace_root="/tmp/repo",
    )
    session.matured_kept = 12
    session.matured_discarded = 8
    session.matured_rolled_back = 3
    session.matured_failed = 2
    assert session.matured_total == 25


def test_summary_from_session_requires_ended_at() -> None:
    session = DeepRunSession.new(
        ends_at=time.time() + 60,
        preset="balanced",
        focus="active_goals",
        horizon_bias="balanced",
        locality="local_preferred",
        cost_cap_usd=0.0,
        workspace_root="/tmp/repo",
    )
    with pytest.raises(ValueError, match="ended_at"):
        DeepRunSummary.from_session(session)


def test_summary_from_session_carries_all_four_counters() -> None:
    session = DeepRunSession.new(
        ends_at=time.time() + 60,
        preset="aggressive",
        focus="active_goals",
        horizon_bias="balanced",
        locality="local_preferred",
        cost_cap_usd=2.0,
        workspace_root="/tmp/repo",
    )
    session.status = "ended"
    session.ended_at = time.time()
    session.matured_kept = 11
    session.matured_discarded = 7
    session.matured_rolled_back = 2
    session.matured_failed = 1
    session.promoted_count = 4
    session.cycles_run = 30
    session.spend_usd = 1.42
    session.pause_reasons = ["thermal", "user_input_observed"]
    summary = DeepRunSummary.from_session(session)
    assert summary.session_id == session.id
    assert summary.matured_kept == 11
    assert summary.matured_discarded == 7
    assert summary.matured_rolled_back == 2
    assert summary.matured_failed == 1
    assert summary.promoted_count == 4
    assert summary.cycles_run == 30
    assert summary.spend_usd == pytest.approx(1.42)
    assert summary.pause_reasons == ("thermal", "user_input_observed")
    assert summary.final_status == "ended"


def test_pass_log_entry_factory_stamps_id_and_pass_at() -> None:
    entry = DeepRunPassLogEntry.new(
        session_id="sess-1",
        prediction_id="pred-1",
        action="matured_kept",
        cycle_index=4,
    )
    assert entry.id  # uuid string, non-empty
    assert entry.pass_at > 0
    assert entry.action == "matured_kept"
    assert entry.cycle_index == 4
    assert entry.before_evidence_score is None
    assert entry.contract_json is None


def test_session_and_pass_id_factories_are_unique() -> None:
    """Two consecutive id generations must differ — required because
    the store uses ``id`` as the primary key and a collision would
    silently drop a record."""
    assert new_deep_run_session_id() != new_deep_run_session_id()
    assert new_deep_run_pass_id() != new_deep_run_pass_id()
