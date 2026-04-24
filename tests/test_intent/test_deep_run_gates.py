# SPDX-License-Identifier: Apache-2.0
"""WS2 — Deep-Run gates tests (0.8.3).

Resource gates, cost gate, locality gate, routing-state singleton.
The gates are pure functions on a probe + an optional active session;
state is managed via process-wide singletons that the engine maintains.
"""

from __future__ import annotations

import threading
import time

import pytest

from vaner.intent.deep_run import DeepRunSession
from vaner.intent.deep_run_gates import (
    NoOpResourceGateProbe,
    ResourceGateConfig,
    cost_gate_spent_usd,
    evaluate_resource_gates,
    get_active_session_for_routing,
    is_remote_call_allowed,
    remaining_cost_budget,
    reset_cost_gate,
    set_active_session_for_routing,
    try_consume_cost,
)


@pytest.fixture(autouse=True)
def _isolate_singletons():
    """Each test starts with a clean routing pointer + cost gate.

    The deep_run_gates module exposes process-wide state for the router
    to consult; tests that mutate it must reset between cases or they
    will bleed state into each other (and into unrelated test files).
    """
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


# ---------------------------------------------------------------------------
# Resource gates
# ---------------------------------------------------------------------------


def test_noop_probe_yields_no_pause_reasons() -> None:
    reasons = evaluate_resource_gates(probe=NoOpResourceGateProbe())
    assert reasons == []


def test_battery_low_pauses() -> None:
    probe = NoOpResourceGateProbe(on_battery=True, battery_pct=20)
    reasons = evaluate_resource_gates(probe=probe)
    assert "battery" in reasons


def test_battery_low_but_plugged_in_does_not_pause() -> None:
    """Charge below threshold but on AC ⇒ no pause. The battery gate
    cares about the *combination* of low + on-battery."""
    probe = NoOpResourceGateProbe(on_battery=False, battery_pct=10)
    reasons = evaluate_resource_gates(probe=probe)
    assert "battery" not in reasons


def test_thermal_throttle_pauses() -> None:
    probe = NoOpResourceGateProbe(thermal_throttled=True)
    assert "thermal" in evaluate_resource_gates(probe=probe)


def test_gpu_temp_above_ceiling_pauses() -> None:
    probe = NoOpResourceGateProbe(gpu_temp=90)
    assert "thermal" in evaluate_resource_gates(probe=probe)


def test_thermal_only_recorded_once_when_both_signals_fire() -> None:
    probe = NoOpResourceGateProbe(thermal_throttled=True, gpu_temp=95)
    reasons = evaluate_resource_gates(probe=probe)
    assert reasons.count("thermal") == 1


def test_thermal_disabled_via_config() -> None:
    probe = NoOpResourceGateProbe(thermal_throttled=True)
    cfg = ResourceGateConfig(cpu_throttle_pause_enabled=False)
    reasons = evaluate_resource_gates(probe=probe, config=cfg)
    # GPU temp is None here so thermal should not appear at all.
    assert "thermal" not in reasons


def test_user_input_recent_pauses() -> None:
    probe = NoOpResourceGateProbe(seconds_idle=10.0)
    cfg = ResourceGateConfig(user_input_pause_grace_seconds=60)
    reasons = evaluate_resource_gates(probe=probe, config=cfg)
    assert "user_input_observed" in reasons


def test_user_input_old_does_not_pause() -> None:
    probe = NoOpResourceGateProbe(seconds_idle=120.0)
    cfg = ResourceGateConfig(user_input_pause_grace_seconds=60)
    reasons = evaluate_resource_gates(probe=probe, config=cfg)
    assert "user_input_observed" not in reasons


def test_engine_error_rate_above_threshold_pauses() -> None:
    probe = NoOpResourceGateProbe(failure_rate=0.20)
    cfg = ResourceGateConfig(engine_error_rate_pause_threshold=0.10)
    reasons = evaluate_resource_gates(probe=probe, config=cfg)
    assert "engine_error_rate" in reasons


def test_multiple_constraints_all_reported() -> None:
    probe = NoOpResourceGateProbe(on_battery=True, battery_pct=15, thermal_throttled=True, seconds_idle=5.0)
    reasons = evaluate_resource_gates(probe=probe)
    assert {"battery", "thermal", "user_input_observed"} <= set(reasons)


# ---------------------------------------------------------------------------
# Cost gate
# ---------------------------------------------------------------------------


def test_cost_gate_no_session_always_allows() -> None:
    """No active session ⇒ try_consume_cost is a no-op pass-through."""
    assert try_consume_cost(0.5) is True
    assert try_consume_cost(100.0) is True


def test_cost_gate_zero_cap_blocks_any_positive_spend() -> None:
    session = _session(cost_cap_usd=0.0)
    reset_cost_gate(session)
    assert try_consume_cost(0.001) is False
    assert try_consume_cost(0.0) is True  # zero-cost calls (cache hits) always ok


def test_cost_gate_consumes_until_cap() -> None:
    session = _session(cost_cap_usd=1.0)
    reset_cost_gate(session)
    assert try_consume_cost(0.4) is True
    assert try_consume_cost(0.4) is True
    assert try_consume_cost(0.3) is False  # would exceed
    assert try_consume_cost(0.2) is True  # fits exactly
    assert remaining_cost_budget() == pytest.approx(0.0)


def test_cost_gate_remaining_budget_tracks_spend() -> None:
    session = _session(cost_cap_usd=2.0)
    reset_cost_gate(session)
    assert remaining_cost_budget() == pytest.approx(2.0)
    try_consume_cost(0.5)
    assert remaining_cost_budget() == pytest.approx(1.5)


def test_cost_gate_is_thread_safe_under_concurrent_consumers() -> None:
    """100 threads each trying to consume $0.05 against a $1.00 cap.

    The contract under test is the *safety* property: the gate never
    overshoots the cap, regardless of concurrent grants. Float drift
    on cumulative $0.05 sums means the gate may grant 19 or 20 calls
    (the 20th fails by ~1e-17 in float arithmetic). Both outcomes
    satisfy the safety property; the wrong outcome would be 21+.
    """
    session = _session(cost_cap_usd=1.0)
    reset_cost_gate(session)
    successes: list[bool] = []
    success_lock = threading.Lock()

    def worker() -> None:
        ok = try_consume_cost(0.05)
        with success_lock:
            successes.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    granted = sum(1 for s in successes if s)
    assert granted in (19, 20), f"granted={granted} — gate overshot the cap"
    spent = cost_gate_spent_usd()
    assert spent is not None
    assert spent <= 1.0 + 1e-9, f"overshot the cap: spent={spent}"


def test_reset_cost_gate_with_none_clears_state() -> None:
    session = _session(cost_cap_usd=1.0)
    reset_cost_gate(session)
    try_consume_cost(0.5)
    reset_cost_gate(None)
    assert remaining_cost_budget() is None
    assert cost_gate_spent_usd() is None


# ---------------------------------------------------------------------------
# Locality gate
# ---------------------------------------------------------------------------


def test_locality_no_session_allows_remote() -> None:
    assert is_remote_call_allowed("https://api.openai.com/v1") is True


def test_locality_local_only_blocks_remote() -> None:
    session = _session(locality="local_only")
    set_active_session_for_routing(session)
    assert is_remote_call_allowed("https://api.openai.com/v1") is False


def test_locality_local_only_allows_local_urls() -> None:
    session = _session(locality="local_only")
    set_active_session_for_routing(session)
    assert is_remote_call_allowed("http://localhost:11434/v1") is True
    assert is_remote_call_allowed("http://127.0.0.1:8080") is True


def test_locality_local_preferred_does_not_hard_block_remote() -> None:
    """local_preferred is an *advisory* preference enforced by the
    router's own fallback logic; the locality gate does not itself
    hard-block remote calls in that mode."""
    session = _session(locality="local_preferred")
    set_active_session_for_routing(session)
    assert is_remote_call_allowed("https://api.openai.com/v1") is True


def test_locality_allow_cloud_unrestricted() -> None:
    session = _session(locality="allow_cloud")
    set_active_session_for_routing(session)
    assert is_remote_call_allowed("https://api.openai.com/v1") is True


# ---------------------------------------------------------------------------
# Routing-state singleton
# ---------------------------------------------------------------------------


def test_routing_singleton_set_and_get() -> None:
    assert get_active_session_for_routing() is None
    session = _session()
    set_active_session_for_routing(session)
    fetched = get_active_session_for_routing()
    assert fetched is not None
    assert fetched.id == session.id
    set_active_session_for_routing(None)
    assert get_active_session_for_routing() is None
