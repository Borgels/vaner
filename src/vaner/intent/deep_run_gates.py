# SPDX-License-Identifier: Apache-2.0
"""WS2 — Deep-Run resource / cost / locality gates (0.8.3).

Three gate families, each implemented as a pure function operating on
an active :class:`DeepRunSession` plus a small probe / state object.
The engine consults the gates each cycle (resource gates) or before
each remote call (cost + locality). All gates are *signals*, not
*actions*: they return what is true; the engine decides what to do.

Resource gates
--------------

A :class:`ResourceGateProbe` Protocol abstracts platform-specific
queries (battery, thermal, GPU temp, foreground input, engine error
rate). The default :class:`NoOpResourceGateProbe` returns "no
constraint" for every method so tests can compose freely; production
code wires a platform-aware implementation (psutil + per-OS calls)
that lives outside this module.

Cost + locality gates
---------------------

Pure functions consult an optional active session. Cost gating uses a
thread-safe in-memory cumulative spend counter (the persisted
``DeepRunSession.spend_usd`` is updated separately by the engine; the
in-memory counter is the per-process arbiter for concurrent calls).

Routing-state singleton
-----------------------

The router (``vaner.router.backends``) needs to consult the active
session to enforce locality and cost without a parameter-passing
refactor through every LLM call. The :func:`set_active_session_for_routing`
/ :func:`get_active_session_for_routing` pair maintains a thread-safe
process-wide pointer the engine updates on session start / stop. When
no session is active the pointer is ``None`` and the router falls
back to its existing behaviour.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Protocol

from vaner.intent.deep_run import DeepRunPauseReason, DeepRunSession

# ---------------------------------------------------------------------------
# Resource gate probe
# ---------------------------------------------------------------------------


class ResourceGateProbe(Protocol):
    """Platform-agnostic resource probe.

    Implementations wrap psutil / OS-specific calls. The default
    :class:`NoOpResourceGateProbe` returns "no constraint" for every
    method; a production probe wired by the daemon returns real
    platform values. Each method is sync and cheap enough to call
    once per cycle.
    """

    def battery_charge_percent(self) -> int | None:
        """Battery charge as 0–100, or ``None`` if no battery present."""

    def is_on_battery(self) -> bool:
        """``True`` when the device is running on battery (not plugged in)."""

    def is_thermal_throttled(self) -> bool:
        """``True`` when the OS reports CPU thermal throttling active."""

    def gpu_temp_celsius(self) -> int | None:
        """Hottest GPU temperature in C, or ``None`` if not measurable."""

    def seconds_since_user_input(self) -> float | None:
        """Seconds since the last foreground user input event, or
        ``None`` if not measurable. ``0.0`` means input is happening
        right now."""

    def cycle_failure_rate(self) -> float:
        """Recent engine cycle failure rate, 0.0–1.0. Used to pause
        Deep-Run when the engine is in trouble rather than silently
        burning compute on broken cycles."""


@dataclass(slots=True)
class NoOpResourceGateProbe:
    """Default probe: every method reports "no constraint."

    Used in tests and on platforms where no probe has been wired yet.
    Production wiring should replace this with a psutil-backed probe
    in the daemon startup path. Mutable in tests via the public
    fields below for easy preset of synthetic resource conditions.
    """

    battery_pct: int | None = None
    on_battery: bool = False
    thermal_throttled: bool = False
    gpu_temp: int | None = None
    seconds_idle: float | None = None
    failure_rate: float = 0.0

    def battery_charge_percent(self) -> int | None:
        return self.battery_pct

    def is_on_battery(self) -> bool:
        return self.on_battery

    def is_thermal_throttled(self) -> bool:
        return self.thermal_throttled

    def gpu_temp_celsius(self) -> int | None:
        return self.gpu_temp

    def seconds_since_user_input(self) -> float | None:
        return self.seconds_idle

    def cycle_failure_rate(self) -> float:
        return self.failure_rate


@dataclass(frozen=True, slots=True)
class ResourceGateConfig:
    """Thresholds for the resource gates. Mirrors
    :data:`vaner.toml [deep_run.guardrails]`. Defaults match spec §16.1."""

    battery_pause_charge_threshold: int = 30
    gpu_temp_pause_ceiling_c: int = 85
    cpu_throttle_pause_enabled: bool = True
    user_input_pause_grace_seconds: int = 60
    engine_error_rate_pause_threshold: float = 0.10


def evaluate_resource_gates(
    *,
    probe: ResourceGateProbe,
    config: ResourceGateConfig | None = None,
) -> list[DeepRunPauseReason]:
    """Return the set of pause reasons currently asserted by the probe.

    Empty list means "no constraints — the session may run." A
    non-empty list means the engine should mark the session
    ``status='paused'`` with these reasons until at least one clears.

    The engine is responsible for the pause/resume *decision*; this
    function only reports facts.
    """

    cfg = config if config is not None else ResourceGateConfig()
    reasons: list[DeepRunPauseReason] = []

    if probe.is_on_battery():
        charge = probe.battery_charge_percent()
        if charge is not None and charge < cfg.battery_pause_charge_threshold:
            reasons.append("battery")

    thermal_hit = False
    if cfg.cpu_throttle_pause_enabled and probe.is_thermal_throttled():
        thermal_hit = True
    gpu_temp = probe.gpu_temp_celsius()
    if gpu_temp is not None and gpu_temp >= cfg.gpu_temp_pause_ceiling_c:
        thermal_hit = True
    if thermal_hit:
        reasons.append("thermal")

    secs = probe.seconds_since_user_input()
    if secs is not None and secs < cfg.user_input_pause_grace_seconds:
        reasons.append("user_input_observed")

    if probe.cycle_failure_rate() > cfg.engine_error_rate_pause_threshold:
        reasons.append("engine_error_rate")

    return reasons


# ---------------------------------------------------------------------------
# Cost gate (thread-safe in-memory cumulative spend)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _CostGateState:
    """Per-session cumulative remote spend counter.

    The persisted ``DeepRunSession.spend_usd`` is the durable record.
    This in-memory counter is the per-process arbiter that prevents
    concurrent calls from each individually thinking they fit under
    the cap and collectively overshooting it.
    """

    session_id: str
    cap_usd: float
    spent_usd: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


_cost_state: _CostGateState | None = None
_cost_state_lock = threading.Lock()


def reset_cost_gate(session: DeepRunSession | None) -> None:
    """Initialise (or clear) the in-memory cost gate for a session.

    Engine calls this on ``start_deep_run`` (with the session) and on
    ``stop_deep_run`` (with ``None``). Calling repeatedly with the
    same session id is idempotent. Calling with a different session
    id swaps the gate to the new session and zeros the counter.
    """

    global _cost_state
    with _cost_state_lock:
        if session is None:
            _cost_state = None
            return
        _cost_state = _CostGateState(
            session_id=session.id,
            cap_usd=float(session.cost_cap_usd),
            spent_usd=float(session.spend_usd),
        )


def try_consume_cost(estimated_usd: float) -> bool:
    """Reserve ``estimated_usd`` against the active cost cap.

    Returns ``True`` if the spend fits and was reserved; ``False`` if
    it would exceed the cap. A return of ``False`` is the engine's
    cue to either skip the call, downgrade to a local backend, or
    pause the session.

    With ``cap_usd == 0.0`` (the safe default) any positive estimate
    returns ``False`` — no remote spend permitted. A zero-cost call
    (e.g. a cache hit) always returns ``True``.

    Thread-safe; safe to call from concurrent backend tasks. Returns
    ``True`` (no-op) when no Deep-Run session is active.

    0.8.4 hardening (HIGH-6): the ``_cost_state`` pointer itself is
    read under ``_cost_state_lock`` so a concurrent
    ``reset_cost_gate(new_session)`` cannot rebind the pointer between
    our read and our ``with state.lock:`` entry. Without this the
    first call after a session swap could charge the *old* session's
    counter and bypass the new session's accounting by one charge.
    """

    with _cost_state_lock:
        state = _cost_state
    if state is None:
        return True
    with state.lock:
        if estimated_usd <= 0.0:
            return True
        if state.cap_usd <= 0.0:
            return False
        if state.spent_usd + estimated_usd > state.cap_usd:
            return False
        state.spent_usd += float(estimated_usd)
        return True


def remaining_cost_budget() -> float | None:
    """Return the remaining budget in USD, or ``None`` if no session.

    See :func:`try_consume_cost` for the rationale behind reading
    ``_cost_state`` under ``_cost_state_lock``.
    """

    with _cost_state_lock:
        state = _cost_state
    if state is None:
        return None
    with state.lock:
        return max(0.0, state.cap_usd - state.spent_usd)


def cost_gate_spent_usd() -> float | None:
    """Return the cumulative in-memory spend, or ``None`` if no session.

    Used by the engine to flush the in-memory counter into the
    persisted ``DeepRunSession.spend_usd`` periodically (durable
    record vs. fast-path arbiter). The two diverge briefly between
    flushes; the persisted value lags the in-memory value.

    See :func:`try_consume_cost` for the rationale behind reading
    ``_cost_state`` under ``_cost_state_lock``.
    """

    with _cost_state_lock:
        state = _cost_state
    if state is None:
        return None
    with state.lock:
        return state.spent_usd


# ---------------------------------------------------------------------------
# Locality gate
# ---------------------------------------------------------------------------


def _is_local_url(base_url: str) -> bool:
    """Mirror of ``vaner.router.backends._is_local_backend`` — kept as
    a duplicate to avoid an intent → router import (router can import
    intent freely; intent should not depend on router)."""

    lowered = base_url.lower()
    return "localhost" in lowered or "127.0.0.1" in lowered or "0.0.0.0" in lowered


def is_remote_call_allowed(base_url: str) -> bool:
    """Return ``True`` if the active session permits a call to ``base_url``.

    - No active session ⇒ always ``True`` (unchanged router behaviour).
    - Active session with ``locality='local_only'`` ⇒ only local URLs.
    - Active session with ``locality in {'local_preferred', 'allow_cloud'}``
      ⇒ always ``True`` (locality preference is enforced by the router's
      pre-existing fallback logic, not by hard gating here).

    Thread-safe; intended to be called by the router from any context.
    """

    session = get_active_session_for_routing()
    if session is None:
        return True
    if session.locality == "local_only":
        return _is_local_url(base_url)
    return True


# ---------------------------------------------------------------------------
# Routing-state singleton
# ---------------------------------------------------------------------------


_routing_session: DeepRunSession | None = None
_routing_session_lock = threading.Lock()


def set_active_session_for_routing(session: DeepRunSession | None) -> None:
    """Publish the active Deep-Run session so the router can consult it.

    Engine calls this on ``start_deep_run`` (with the session) and on
    ``stop_deep_run`` (with ``None``). Also called by
    ``_resume_deep_run_on_restart`` when an in-flight session is
    restored from the store.

    The router reads the pointer via :func:`get_active_session_for_routing`
    in the cost / locality gate paths.
    """

    global _routing_session
    with _routing_session_lock:
        _routing_session = session


def get_active_session_for_routing() -> DeepRunSession | None:
    """Return the active session pointer, or ``None`` if no session."""

    with _routing_session_lock:
        return _routing_session


__all__ = [
    "NoOpResourceGateProbe",
    "ResourceGateConfig",
    "ResourceGateProbe",
    "cost_gate_spent_usd",
    "evaluate_resource_gates",
    "get_active_session_for_routing",
    "is_remote_call_allowed",
    "remaining_cost_budget",
    "reset_cost_gate",
    "set_active_session_for_routing",
    "try_consume_cost",
]
