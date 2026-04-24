# SPDX-License-Identifier: Apache-2.0
"""WS7 — WorkspaceGoal data model.

A goal is a workspace-level aspiration that spans many prompts and
cycles: *"implement JWT migration"*, *"fix the auth-token leak"*, *"add
unit coverage for the parser"*. Predictions are per-turn next-move
guesses; goals are the user's long-horizon intent. Vaner holds them
so long-running prepared work (a multi-cycle briefing, an
architectural review) can be anchored to something more stable than a
single-prompt hypothesis.

The dataclass mirrors the ``spec`` / ``run`` / ``evidence`` partition
used by :class:`PredictedPrompt` for UX consistency — callers can
introspect identity, mutable state, and evidence independently.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Literal

GoalSource = Literal["branch_name", "commit_cluster", "query_cluster", "user_declared"]
GoalStatus = Literal["active", "paused", "abandoned", "achieved"]


def goal_id(source: str, title: str) -> str:
    """Stable hash over (source, title). The identity key for a goal."""
    payload = f"{source}|{title}".encode()
    return hashlib.sha1(payload).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class GoalEvidence:
    """A single piece of evidence linking a goal to observable state.

    ``kind`` identifies the evidence type (``"commit_sha"``,
    ``"query_id"``, ``"file_path"``, ``"branch_name"``). ``value`` is
    the identifier itself. ``weight`` reflects how strongly this piece
    of evidence supports the goal — higher is more supportive.
    """

    kind: Literal["commit_sha", "query_id", "file_path", "branch_name"]
    value: str
    weight: float = 1.0


@dataclass(slots=True)
class WorkspaceGoal:
    """A workspace-level goal: identity + mutable status + evidence.

    Attributes mirror the SQLite ``workspace_goals`` schema:

    - ``id`` — sha1(source|title) — stable across cycles.
    - ``title`` — human-readable; shown in the UI.
    - ``description`` — optional longer form (``""`` when only the
      title was inferred).
    - ``source`` — where the goal came from; drives invalidation.
    - ``confidence`` — 0.0–1.0, decays on evidence staleness.
    - ``status`` — user-visible lifecycle state.
    - ``created_at`` / ``last_observed_at`` — timestamps.
    - ``evidence`` — supporting observations (commits, queries, paths).
    - ``related_files`` — paths associated with the goal (passes into
      scenario scoring as a priority hint).
    """

    id: str
    title: str
    description: str
    source: GoalSource
    confidence: float
    status: GoalStatus = "active"
    created_at: float = field(default_factory=time.time)
    last_observed_at: float = field(default_factory=time.time)
    evidence: list[GoalEvidence] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)

    @classmethod
    def from_hint(
        cls,
        *,
        title: str,
        source: GoalSource,
        confidence: float,
        description: str = "",
        evidence: list[GoalEvidence] | None = None,
        related_files: list[str] | None = None,
    ) -> WorkspaceGoal:
        """Build a goal from inference-layer output. Factory helper so
        callers don't have to compute the id themselves."""
        gid = goal_id(source, title)
        return cls(
            id=gid,
            title=title,
            description=description,
            source=source,
            confidence=confidence,
            evidence=list(evidence or []),
            related_files=list(related_files or []),
        )
