# SPDX-License-Identifier: Apache-2.0
"""Core :class:`EventBus` + :class:`VanerEvent` implementation."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Canonical stage names. Events outside this set are accepted (forward-compat)
# but the cockpit pipeline view will only route known stages into lanes.
STAGES: tuple[str, ...] = (
    "signals",
    "targets",
    "model",
    "artefacts",
    "scenarios",
    "decisions",
    "system",
)

# Legacy tag -> CSS colour mapping preserved from the original
# ``ScenarioStore._publish`` implementation so existing consumers of
# ``/events/stream`` (including the current cockpit build) keep rendering
# without change. New structured consumers should key off ``stage`` + ``kind``.
_LEGACY_COLORS = {
    "score": "var(--fg-3)",
    "expand": "var(--accent)",
    "user": "var(--amber)",
    "pin": "var(--amber)",
    "stale": "var(--fg-4)",
    "cycle": "var(--accent)",
    "target": "var(--fg-3)",
    "llm": "var(--amber)",
    "artefact": "var(--fg-3)",
    "decision": "var(--accent)",
    "signal": "var(--fg-4)",
}


def _format_hms(ts: float) -> str:
    """Format a wall-clock timestamp as ``MM:SS.cc`` (legacy cockpit shape)."""
    minutes = int(ts // 60) % 60
    seconds = int(ts) % 60
    centiseconds = int((ts - int(ts)) * 100)
    return f"{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


@dataclass(slots=True)
class VanerEvent:
    """A single pipeline event.

    ``stage`` is one of :data:`STAGES`; ``kind`` is a dotted sub-kind
    (e.g. ``cycle.start``, ``llm.request``). ``payload`` is free-form JSON.
    ``scn``, ``path``, and ``cycle_id`` are optional correlation fields the
    cockpit uses to join events across lanes (e.g. pulse the scenario node
    when an ``llm.response`` arrives whose ``path`` matches).
    """

    stage: str
    kind: str
    ts: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)
    scn: str | None = None
    path: str | None = None
    cycle_id: str | None = None
    id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:8]}")

    def legacy_tag(self) -> str:
        """First segment of ``kind`` — stable key for the legacy envelope."""
        return self.kind.split(".", 1)[0] if self.kind else self.stage

    def legacy_message(self) -> str:
        msg = self.payload.get("msg") if isinstance(self.payload, dict) else None
        if isinstance(msg, str) and msg:
            return msg
        if self.scn:
            return f"{self.scn} {self.kind}"
        if self.path:
            return f"{self.path} {self.kind}"
        return f"{self.stage} {self.kind}"

    def to_dict(self) -> dict[str, Any]:
        """Wire format emitted by ``/events/stream``.

        Fields prefixed ``stage``/``kind``/``payload`` are the new structured
        contract. Fields ``tag``/``t``/``color``/``msg``/``scn`` are preserved
        for one release so older cockpit bundles / external scripts that read
        the legacy envelope keep working during the pipeline-view rollout.
        """
        tag = self.legacy_tag()
        msg = self.legacy_message()
        return {
            "id": self.id,
            "ts": self.ts,
            "stage": self.stage,
            "kind": self.kind,
            "payload": dict(self.payload),
            "scn": self.scn,
            "path": self.path,
            "cycle_id": self.cycle_id,
            # Legacy envelope (kept for backward compatibility).
            "t": _format_hms(self.ts),
            "tag": tag,
            "color": _LEGACY_COLORS.get(tag, "var(--fg-3)"),
            "msg": msg,
        }


class EventBus:
    """In-memory fan-out bus using bounded asyncio queues.

    Subscribers receive a queue of :class:`VanerEvent`. The queue is bounded
    (``queue_size``) and overflow drops the slow subscriber entirely rather
    than blocking the publisher. This mirrors the behaviour of the legacy
    ``ScenarioStore._subscribers`` set and prevents a slow SSE client from
    stalling daemon progress.
    """

    def __init__(self, queue_size: int = 256) -> None:
        self._subscribers: set[asyncio.Queue[VanerEvent | None]] = set()
        self._queue_size = queue_size

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[VanerEvent | None]:
        queue: asyncio.Queue[VanerEvent | None] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[VanerEvent | None]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: VanerEvent) -> None:
        if not self._subscribers:
            return
        stale: list[asyncio.Queue[VanerEvent | None]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                stale.append(queue)
            except Exception:  # pragma: no cover - defensive
                logger.debug("event bus: dropping subscriber after publish failure", exc_info=True)
                stale.append(queue)
        for queue in stale:
            self._subscribers.discard(queue)

    async def close(self) -> None:
        """Send a terminal ``None`` sentinel to every subscriber and drop them."""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._subscribers.clear()


_bus: EventBus | None = None


def get_bus() -> EventBus:
    """Return the process-global :class:`EventBus`, creating it on demand."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus() -> None:
    """Replace the singleton bus with a fresh one. For tests only."""
    global _bus
    _bus = EventBus()


_current_cycle_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("vaner_current_cycle_id", default=None)


@contextmanager
def cycle_scope(cycle_id: str | None) -> Iterator[None]:
    """Bind ``cycle_id`` to the active context so :func:`publish` can attach
    it automatically to events emitted from deeply nested callsites (LLM
    helpers, artefact store writes). Restores the previous binding on exit.
    """
    token = _current_cycle_id.set(cycle_id)
    try:
        yield
    finally:
        _current_cycle_id.reset(token)


def current_cycle_id() -> str | None:
    """Return the cycle id bound by an enclosing :func:`cycle_scope` (if any)."""
    return _current_cycle_id.get()


def publish(
    stage: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    scn: str | None = None,
    path: str | None = None,
    cycle_id: str | None = None,
) -> VanerEvent:
    """Build a :class:`VanerEvent` and broadcast it on the global bus.

    Returns the event so emitters can correlate follow-ups (e.g. capture an
    ``llm.request`` id to match the subsequent ``llm.response``). When
    ``cycle_id`` is not supplied the current :func:`cycle_scope` binding (if
    any) is used so daemon-cycle correlation works without threading the id
    through every helper.
    """
    event = VanerEvent(
        stage=stage,
        kind=kind,
        ts=time.time(),
        payload=dict(payload or {}),
        scn=scn,
        path=path,
        cycle_id=cycle_id if cycle_id is not None else _current_cycle_id.get(),
    )
    get_bus().publish(event)
    return event
