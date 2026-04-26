# SPDX-License-Identifier: Apache-2.0
"""In-process distribution point for composer-lifecycle snapshots.

WS3 wires :meth:`vaner.engine.Engine.observe` so that ``SignalEvent``s
of kind ``composer_lifecycle`` are validated into a
:class:`DraftIntentSnapshot` and published to a ``ComposerSignalPump``.
Subscribers register a coroutine callback; ``publish()`` fires all
subscribers concurrently and isolates failures so one bad subscriber
cannot block the others.

The pump is deliberately minimal — no queue, no buffering, no ordering
guarantees beyond "earlier publish() calls complete their gather()
before later ones start, on the same publish-task." The engine owns
the pump and it lives for the lifetime of the engine.

NOTE (v0.8.8 wiring gap): in v0.8.7 the pump publishes snapshots but
NO production code subscribes to it. The intended v0.8.8 subscriber is
the prediction registry's update path: when a composer signal arrives,
either create a ``composer_intent``-sourced spec (if none for that
session_id exists) or update the existing one's
``compose_signal_strength`` based on the snapshot's lifecycle_state
and confidence. Without that subscriber, ``composer_intent`` specs
never appear in the registry — the entire engine plumbing in WS4/WS5/WS8
(source literal, frontier multiplier, MCP card field) is data-backbone
only until v0.8.8.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from vaner.signals.composer.contract import DraftIntentSnapshot

logger = logging.getLogger(__name__)

ComposerSubscriber = Callable[[DraftIntentSnapshot], Awaitable[None]]


class ComposerSignalPump:
    def __init__(self) -> None:
        self._subscribers: list[ComposerSubscriber] = []

    def subscribe(self, callback: ComposerSubscriber) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: ComposerSubscriber) -> None:
        try:
            self._subscribers.remove(callback)
        except ValueError:
            # Idempotent unsubscribe: callback already removed.
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, snapshot: DraftIntentSnapshot) -> None:
        if not self._subscribers:
            return
        # 0.8.7 hardening M5: snapshot the subscriber list ONCE before
        # gather() so the same tuple drives both the invocation set and
        # the failure-attribution zip below. A subscriber that
        # un/subscribes during dispatch (or another subscriber doing it
        # on its behalf) would otherwise mis-pair callbacks with results.
        subs = tuple(self._subscribers)
        results = await asyncio.gather(
            *(self._invoke(cb, snapshot) for cb in subs),
            return_exceptions=True,
        )
        for cb, result in zip(subs, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "composer signal subscriber %r failed: %s",
                    getattr(cb, "__qualname__", cb),
                    result,
                )

    async def _invoke(self, callback: ComposerSubscriber, snapshot: DraftIntentSnapshot) -> None:
        await callback(snapshot)
