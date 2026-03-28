from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class PrepTrigger:
    context_key: str
    active_files: list[str]
    branch: str
    reason: Literal["file_changed", "git_commit", "branch_switch", "manual"]
    timestamp: float = field(default_factory=time.monotonic)


class Debouncer:
    """Blocks triggers with the same context_key within min_interval_seconds."""

    def __init__(self, min_interval_seconds: float = 5.0):
        self._min_interval = min_interval_seconds
        self._last_seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def should_trigger(self, trigger: PrepTrigger) -> bool:
        with self._lock:
            now = time.monotonic()
            last = self._last_seen.get(trigger.context_key, 0.0)
            if now - last >= self._min_interval:
                self._last_seen[trigger.context_key] = now
                return True
            return False
