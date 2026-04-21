# SPDX-License-Identifier: Apache-2.0
"""Unified event bus for Vaner.

Provides :class:`VanerEvent` and :class:`EventBus`, plus module-level helpers
(:func:`get_bus`, :func:`publish`, :func:`reset_bus`) for broadcasting
pipeline activity (daemon cycles, LLM calls, artefact writes, scenario
mutations, proxy decisions) to any subscriber (UI SSE, metrics, logs).

The bus is process-global: the cockpit server, daemon runner, and proxy all
run in the same Python process when ``vaner daemon serve-http`` or
``vaner proxy`` is started, so a single in-memory bus is sufficient for the
live pipeline view in the cockpit. Tests should call :func:`reset_bus` in a
fixture to get a clean bus per-test.
"""

from __future__ import annotations

from vaner.events.bus import (
    STAGES,
    EventBus,
    VanerEvent,
    current_cycle_id,
    cycle_scope,
    get_bus,
    publish,
    reset_bus,
)

__all__ = [
    "STAGES",
    "EventBus",
    "VanerEvent",
    "current_cycle_id",
    "cycle_scope",
    "get_bus",
    "publish",
    "reset_bus",
]
