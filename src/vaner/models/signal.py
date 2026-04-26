# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pydantic import BaseModel, Field

# Composer-adapter lifecycle events (0.8.7 WS3). The engine's observe()
# branches on this kind to validate the payload as a DraftIntentSnapshot
# and publish to the composer signal pump.
KIND_COMPOSER_LIFECYCLE = "composer_lifecycle"


class SignalEvent(BaseModel):
    id: str
    source: str
    kind: str
    timestamp: float
    payload: dict[str, object] = Field(default_factory=dict)
