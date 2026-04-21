# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pydantic import BaseModel, Field


class SignalEvent(BaseModel):
    id: str
    source: str
    kind: str
    timestamp: float
    payload: dict[str, object] = Field(default_factory=dict)
