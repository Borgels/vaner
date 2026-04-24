# SPDX-License-Identifier: Apache-2.0
"""WS2 — Engine-level gate evaluation tests (0.8.3).

End-to-end-ish integration: the engine's ``evaluate_deep_run_gates``
must reflect the active session's pause state in both the in-memory
cache AND the persisted store row, so the cockpit / desktop / CLI all
see the same truth.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.intent.deep_run_gates import (
    NoOpResourceGateProbe,
    ResourceGateConfig,
    reset_cost_gate,
    set_active_session_for_routing,
)
from vaner.store import deep_run as deep_run_store

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolate_singletons():
    set_active_session_for_routing(None)
    reset_cost_gate(None)
    yield
    set_active_session_for_routing(None)
    reset_cost_gate(None)


async def _stub_llm(_prompt: str) -> str:
    return '{"ranked_files": [], "semantic_intent": "", "confidence": 0.0, "follow_on": []}'


def _seed_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "sample.py").write_text("def hi():\n    return 'hi'\n")


def _engine(repo_root: Path) -> VanerEngine:
    _seed_repo(repo_root)
    engine = VanerEngine(adapter=CodeRepoAdapter(repo_root), llm=_stub_llm)
    engine.config.compute.idle_only = False
    return engine


# ---------------------------------------------------------------------------
# evaluate_deep_run_gates
# ---------------------------------------------------------------------------


async def test_no_session_means_no_constraints(tmp_path) -> None:
    engine = _engine(tmp_path / "repo")
    reasons = await engine.evaluate_deep_run_gates()
    assert reasons == []


async def test_default_probe_means_no_pause(tmp_path) -> None:
    engine = _engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 3600)
    reasons = await engine.evaluate_deep_run_gates()
    assert reasons == []
    cached = await engine.current_deep_run()
    assert cached is not None
    assert cached.status == "active"
    assert cached.pause_reasons == []


async def test_battery_low_flips_status_to_paused(tmp_path) -> None:
    engine = _engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 3600)
    probe = NoOpResourceGateProbe(on_battery=True, battery_pct=15)
    engine.set_resource_gate_probe(probe)

    reasons = await engine.evaluate_deep_run_gates()
    assert "battery" in reasons
    cached = await engine.current_deep_run()
    assert cached is not None
    assert cached.status == "paused"
    assert cached.pause_reasons == reasons

    # Persisted row matches the in-memory cache (cockpit-canonical
    # invariant: every surface reads the same record).
    persisted = await deep_run_store.get_session(engine.store.db_path, cached.id)
    assert persisted is not None
    assert persisted.status == "paused"
    assert persisted.pause_reasons == reasons


async def test_constraints_clearing_resumes_session(tmp_path) -> None:
    engine = _engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 3600)
    probe = NoOpResourceGateProbe(on_battery=True, battery_pct=15)
    engine.set_resource_gate_probe(probe)
    await engine.evaluate_deep_run_gates()

    # User plugs the laptop back in mid-session.
    probe.on_battery = False
    reasons = await engine.evaluate_deep_run_gates()
    assert reasons == []
    cached = await engine.current_deep_run()
    assert cached is not None
    assert cached.status == "active"
    assert cached.pause_reasons == []


async def test_unchanged_constraints_do_not_rewrite(tmp_path) -> None:
    """Two consecutive evaluations with the same probe state should
    be a no-op on the store (avoids spurious DB writes per cycle)."""
    engine = _engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 3600)
    engine.set_resource_gate_probe(NoOpResourceGateProbe(on_battery=True, battery_pct=15))
    first = await engine.evaluate_deep_run_gates()
    second = await engine.evaluate_deep_run_gates()
    assert first == second
    cached = await engine.current_deep_run()
    assert cached is not None
    assert cached.status == "paused"


async def test_custom_resource_gate_config_overrides_thresholds(tmp_path) -> None:
    engine = _engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 3600)
    probe = NoOpResourceGateProbe(on_battery=True, battery_pct=25)
    engine.set_resource_gate_probe(
        probe,
        config=ResourceGateConfig(battery_pause_charge_threshold=20),
    )
    # Now the threshold is 20%, charge is 25% → no pause.
    reasons = await engine.evaluate_deep_run_gates()
    assert "battery" not in reasons


# ---------------------------------------------------------------------------
# start_deep_run / stop_deep_run publish to the routing singleton
# ---------------------------------------------------------------------------


async def test_start_publishes_to_routing_singleton(tmp_path) -> None:
    from vaner.intent.deep_run_gates import get_active_session_for_routing

    engine = _engine(tmp_path / "repo")
    assert get_active_session_for_routing() is None
    started = await engine.start_deep_run(ends_at=time.time() + 60)
    fetched = get_active_session_for_routing()
    assert fetched is not None
    assert fetched.id == started.id


async def test_stop_clears_routing_singleton(tmp_path) -> None:
    from vaner.intent.deep_run_gates import get_active_session_for_routing

    engine = _engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 60)
    await engine.stop_deep_run()
    assert get_active_session_for_routing() is None


async def test_resume_on_restart_republishes_to_routing(tmp_path) -> None:
    """A daemon restart with an in-flight session should re-arm the
    routing singleton so the router enforces gates again from cycle
    one — not just after the user issues a new start command."""
    from vaner.intent.deep_run_gates import get_active_session_for_routing

    repo = tmp_path / "repo"
    engine_a = _engine(repo)
    started = await engine_a.start_deep_run(ends_at=time.time() + 3600, locality="local_only")
    set_active_session_for_routing(None)  # simulate process restart

    engine_b = _engine(repo)
    cached = await engine_b.current_deep_run()
    assert cached is not None
    assert cached.id == started.id
    fetched = get_active_session_for_routing()
    assert fetched is not None
    assert fetched.id == started.id
    assert fetched.locality == "local_only"


# ---------------------------------------------------------------------------
# flush_deep_run_cost_to_store
# ---------------------------------------------------------------------------


async def test_flush_cost_persists_in_memory_spend(tmp_path) -> None:
    from vaner.intent.deep_run_gates import try_consume_cost

    engine = _engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 3600, locality="allow_cloud", cost_cap_usd=2.0)
    # Simulate three remote calls consuming budget.
    assert try_consume_cost(0.20) is True
    assert try_consume_cost(0.30) is True
    assert try_consume_cost(0.10) is True
    persisted_spend = await engine.flush_deep_run_cost_to_store()
    assert persisted_spend == pytest.approx(0.60)
    cached = await engine.current_deep_run()
    assert cached is not None
    assert cached.spend_usd == pytest.approx(0.60)


# ---------------------------------------------------------------------------
# increment_deep_run_cycle_counter
# ---------------------------------------------------------------------------


async def test_cycle_counter_increments(tmp_path) -> None:
    engine = _engine(tmp_path / "repo")
    await engine.start_deep_run(ends_at=time.time() + 60)
    await engine.increment_deep_run_cycle_counter()
    await engine.increment_deep_run_cycle_counter(promoted_count=3)
    cached = await engine.current_deep_run()
    assert cached is not None
    assert cached.cycles_run == 2
    assert cached.promoted_count == 3
