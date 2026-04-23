# SPDX-License-Identifier: Apache-2.0
"""Value object tracking how the user queries Vaner over time.

Persistence lives in :class:`vaner.store.profile_store.UserProfileStore`.
Prior to 0.8.0 this module also handled JSON persistence; that path was
replaced by SQLite for durability under concurrent access.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class UserProfile:
    pace_ema_seconds: float = 0.0
    pivot_rate: float = 0.0
    depth_preference: float = 0.0
    mode_mix: dict[str, float] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)
    _last_query_ts: float = 0.0
    _query_count: int = 0
    _pivots: int = 0
    _last_mode: str = ""

    def observe(self, *, mode: str, depth: int, ts: float | None = None) -> None:
        now = float(ts) if ts is not None else time.time()
        if self._last_query_ts > 0 and now > self._last_query_ts:
            gap = now - self._last_query_ts
            alpha = 0.35
            if self.pace_ema_seconds <= 0.0:
                self.pace_ema_seconds = gap
            else:
                self.pace_ema_seconds = (alpha * gap) + ((1.0 - alpha) * self.pace_ema_seconds)
        self._last_query_ts = now
        self._query_count += 1
        if self._last_mode and self._last_mode != mode:
            self._pivots += 1
        self._last_mode = mode
        self.depth_preference = ((self.depth_preference * max(0, self._query_count - 1)) + float(depth)) / max(1, self._query_count)
        self.mode_mix[mode] = self.mode_mix.get(mode, 0.0) + 1.0
        total_modes = max(1.0, sum(self.mode_mix.values()))
        self.mode_mix = {key: value / total_modes for key, value in self.mode_mix.items()}
        self.pivot_rate = self._pivots / max(1, self._query_count - 1)
        self.updated_at = now
