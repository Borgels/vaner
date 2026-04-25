# SPDX-License-Identifier: Apache-2.0
"""In-process distribution point for composer-lifecycle snapshots.

WS3 wires :meth:`vaner.engine.Engine.observe` so that ``SignalEvent``s
of kind ``composer_lifecycle`` are validated into a
:class:`DraftIntentSnapshot` and published to a ``ComposerSignalPump``.
Subscribers (e.g. the prediction registry's invalidation path in WS4)
register a coroutine callback; ``publish()`` fires all subscribers
concurrently and isolates failures so one bad subscriber cannot block
the others.

The pump is deliberately minimal — no queue, no buffering, no ordering
guarantees beyond "earlier publish() calls complete their gather()
before later ones start, on the same publish-task." The engine owns
the pump and it lives for the lifetime of the engine.
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
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, snapshot: DraftIntentSnapshot) -> None:
        if not self._subscribers:
            return
        results = await asyncio.gather(
            *(self._invoke(cb, snapshot) for cb in tuple(self._subscribers)),
            return_exceptions=True,
        )
        for cb, result in zip(self._subscribers, results, strict=False):
            if isinstance(result, BaseException):
                logger.warning(
                    "composer signal subscriber %r failed: %s",
                    getattr(cb, "__qualname__", cb),
                    result,
                )

    async def _invoke(self, callback: ComposerSubscriber, snapshot: DraftIntentSnapshot) -> None:
        await callback(snapshot)
