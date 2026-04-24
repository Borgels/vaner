# SPDX-License-Identifier: Apache-2.0
"""WS2 — Router-layer Deep-Run gate integration tests (0.8.3).

The router's ``_enforce_deep_run_gates_for_remote`` helper is the
hard-block surface for locality + cost. These tests verify the four
contracts that matter at the router boundary:

1. No active session ⇒ no-op (existing behaviour preserved).
2. Local URL ⇒ no-op (gates only fire on remote calls).
3. local_only session + remote URL ⇒ DeepRunRemoteCallBlockedError.
4. cost cap exhausted + remote URL ⇒ DeepRunRemoteCallBlockedError.
"""

from __future__ import annotations

import time

import pytest

from vaner.intent.deep_run import DeepRunSession
from vaner.intent.deep_run_gates import (
    reset_cost_gate,
    set_active_session_for_routing,
)
from vaner.router.backends import (
    DeepRunRemoteCallBlockedError,
    _enforce_deep_run_gates_for_remote,
)


@pytest.fixture(autouse=True)
def _isolate_singletons():
    set_active_session_for_routing(None)
    reset_cost_gate(None)
    yield
    set_active_session_for_routing(None)
    reset_cost_gate(None)


def _session(**overrides: object) -> DeepRunSession:
    defaults: dict[str, object] = {
        "ends_at": time.time() + 3600,
        "preset": "balanced",
        "focus": "active_goals",
        "horizon_bias": "balanced",
        "locality": "local_preferred",
        "cost_cap_usd": 0.0,
        "workspace_root": "/tmp/repo",
    }
    defaults.update(overrides)
    return DeepRunSession.new(**defaults)  # type: ignore[arg-type]


def test_no_session_is_noop() -> None:
    """Whole point of the gate's safety design: zero behaviour change
    when no Deep-Run session is active."""
    _enforce_deep_run_gates_for_remote("https://api.openai.com/v1")  # no raise


def test_local_url_is_noop_even_with_local_only_session() -> None:
    session = _session(locality="local_only")
    set_active_session_for_routing(session)
    reset_cost_gate(session)
    _enforce_deep_run_gates_for_remote("http://127.0.0.1:11434/v1")  # no raise
    _enforce_deep_run_gates_for_remote("http://localhost:8080")  # no raise


def test_local_only_blocks_remote_with_clear_error() -> None:
    session = _session(locality="local_only")
    set_active_session_for_routing(session)
    reset_cost_gate(session)
    with pytest.raises(DeepRunRemoteCallBlockedError) as excinfo:
        _enforce_deep_run_gates_for_remote("https://api.openai.com/v1")
    assert "local_only" in str(excinfo.value)
    assert session.id in str(excinfo.value)


def test_cost_cap_exhausted_blocks_remote() -> None:
    session = _session(locality="allow_cloud", cost_cap_usd=0.0)
    set_active_session_for_routing(session)
    reset_cost_gate(session)
    with pytest.raises(DeepRunRemoteCallBlockedError) as excinfo:
        _enforce_deep_run_gates_for_remote("https://api.openai.com/v1")
    assert "cost cap" in str(excinfo.value)


def test_cost_cap_with_room_permits_call() -> None:
    session = _session(locality="allow_cloud", cost_cap_usd=1.0)
    set_active_session_for_routing(session)
    reset_cost_gate(session)
    # Default per-call estimate is $0.01 — well within $1.00.
    _enforce_deep_run_gates_for_remote("https://api.openai.com/v1")  # no raise


def test_cost_cap_explicit_estimate_overrides_default() -> None:
    session = _session(locality="allow_cloud", cost_cap_usd=0.05)
    set_active_session_for_routing(session)
    reset_cost_gate(session)
    # An explicit estimate above the cap blocks immediately.
    with pytest.raises(DeepRunRemoteCallBlockedError):
        _enforce_deep_run_gates_for_remote("https://api.openai.com/v1", estimated_usd=0.10)
