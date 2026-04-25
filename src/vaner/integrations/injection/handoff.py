# SPDX-License-Identifier: Apache-2.0
"""Adopt-handoff file-drop reader.

The macOS and Linux desktop clients (and in future, web cockpit / VS Code
extension) stash the Resolution JSON at a platform-canonical path when the
user clicks Adopt. This module is the daemon-side reader that lets the MCP
`vaner.resolve` handler short-circuit when a fresh adopted package is
already on disk for this user.

Paths mirror the Rust `vaner_contract::handoff` module (see
`crates/vaner-contract/src/handoff.rs`):

- Linux:   `$XDG_STATE_HOME/vaner/pending-adopt.json`
  (spec-compliant fallback `~/.local/state/vaner/pending-adopt.json`)
- macOS:   `~/Library/Application Support/Vaner/pending-adopt.json`
- Windows: `%LOCALAPPDATA%\\Vaner\\pending-adopt.json`
- Fallback: `~/.vaner/pending-adopt.json`

Consumers should ignore drops older than `DEFAULT_TTL_SECONDS` (CONTRACT.md
recommends 10 min). The stash writer injects a top-level `stashed_at`
epoch-seconds key exactly for this purpose.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 600
"""10-minute freshness window — matches CONTRACT.md recommendation."""


@dataclass(frozen=True)
class HandoffResolution:
    """A fresh adopted-package drop suitable for suppressing a resolve call."""

    raw: dict[str, Any]
    """Full resolution JSON as written by the desktop client (includes any
    unknown-to-daemon keys — we pass through intact)."""

    stashed_at: float
    """Epoch seconds from the drop's `stashed_at` field."""

    age_seconds: float
    """How long ago the drop was written, at read time."""

    path: Path
    """Which file we read from — useful in logs."""

    @property
    def intent(self) -> str | None:
        v = self.raw.get("intent")
        return v if isinstance(v, str) else None

    @property
    def adopted_from_prediction_id(self) -> str | None:
        v = self.raw.get("adopted_from_prediction_id")
        return v if isinstance(v, str) else None

    @property
    def resolution_id(self) -> str | None:
        v = self.raw.get("resolution_id")
        return v if isinstance(v, str) else None

    @property
    def prepared_briefing(self) -> str | None:
        v = self.raw.get("prepared_briefing")
        return v if isinstance(v, str) else None

    @property
    def predicted_response(self) -> str | None:
        v = self.raw.get("predicted_response")
        return v if isinstance(v, str) else None


def handoff_path() -> Path:
    """Return the platform-canonical handoff file path."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        return base / "Vaner" / "pending-adopt.json"
    if sys.platform.startswith("linux"):
        state = os.environ.get("XDG_STATE_HOME")
        if state:
            root = Path(state)
        else:
            root = Path.home() / ".local" / "state"
        return root / "vaner" / "pending-adopt.json"
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "Vaner" / "pending-adopt.json"
    # Fallback — matches the Rust crate's non-primary-platform branch.
    return Path.home() / ".vaner" / "pending-adopt.json"


def read_handoff(
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
    path: Path | None = None,
) -> HandoffResolution | None:
    """Return the stashed Resolution if it exists and is fresh, else None.

    ``now`` is injectable for tests. ``path`` can override platform default
    (e.g. in-repo test path via ``vaner integrations doctor --repo-root``).
    """
    target = path if path is not None else handoff_path()
    if not target.exists():
        return None
    try:
        raw_text = target.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        logger.info("handoff read failed: %s (path=%s)", exc, target)
        return None
    if not isinstance(data, dict):
        logger.debug("handoff payload is not an object (path=%s)", target)
        return None
    stashed_at_raw = data.get("stashed_at")
    if not isinstance(stashed_at_raw, (int, float)):
        # Older drops without stashed_at are ignored — the desktop clients
        # started injecting this key in 0.8.4; a missing key means either
        # a pre-0.8.4 drop (treat as stale) or a broken writer.
        logger.debug("handoff payload missing stashed_at (path=%s)", target)
        return None
    stashed_at = float(stashed_at_raw)
    current = float(now) if now is not None else time.time()
    age = max(0.0, current - stashed_at)
    if age > ttl_seconds:
        logger.debug("handoff stale: age=%.1fs ttl=%ds (path=%s)", age, ttl_seconds, target)
        return None
    return HandoffResolution(raw=data, stashed_at=stashed_at, age_seconds=age, path=target)


def consume_handoff(
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
    path: Path | None = None,
) -> HandoffResolution | None:
    """Read + delete the handoff file in a single step (one-shot semantics).

    Used by the MCP resolve handler so a single adopted package doesn't
    keep suppressing fresh queries forever. If read fails, no delete.
    """
    result = read_handoff(ttl_seconds=ttl_seconds, now=now, path=path)
    if result is None:
        return None
    try:
        result.path.unlink()
    except OSError as exc:
        logger.info("handoff consumed but delete failed: %s (path=%s)", exc, result.path)
    return result
