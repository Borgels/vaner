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

GoalSource = Literal[
    "branch_name",
    "commit_cluster",
    "query_cluster",
    "user_declared",
    # 0.8.2 WS2 additions. ``artefact_declared`` means an intent-bearing
    # artefact (see :mod:`vaner.intent.artefacts`) explicitly names this
    # goal in its top-level heading or title. ``artefact_inferred`` means
    # the artefact's item structure implies the goal but doesn't state it
    # verbatim. The distinction drives confidence calibration in
    # :func:`vaner.intent.goal_inference.merge_hints`.
    "artefact_declared",
    "artefact_inferred",
]
GoalStatus = Literal[
    "active",
    "paused",
    "abandoned",
    "achieved",
    # 0.8.2 WS2/WS3 additions for reconciliation-driven lifecycle
    # states. ``dormant`` — no corroborating signal for N cycles;
    # ``stale`` — a newer artefact supersedes; ``contradicted`` —
    # reconciliation flagged a divergence between plan and observed
    # progress. Spec §6.2.
    "dormant",
    "stale",
    "contradicted",
]


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

    kind: Literal[
        "commit_sha",
        "query_id",
        "file_path",
        "branch_name",
        # 0.8.2 WS2: evidence drawn from an intent-bearing artefact item
        # (spec §8). Value is the ``IntentArtefactItem.id``; weight
        # reflects the item's own confidence × state weight.
        "artefact_item",
    ]
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
    # 0.8.2 WS2 additions. ``artefact_refs`` lists
    # :class:`IntentArtefact` ids backing this goal; ``subgoal_of`` is
    # the parent goal id when this goal was decomposed from an outline
    # item; the ``pc_*`` fields implement the §6.6 policy-consumer
    # metadata block (``status`` and ``confidence`` above already serve
    # the block directly, so they aren't duplicated here).
    artefact_refs: list[str] = field(default_factory=list)
    subgoal_of: str | None = None
    pc_freshness: float = 1.0
    pc_reconciliation_state: str = "unreconciled"
    pc_unfinished_item_state: str = "none"

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
        artefact_refs: list[str] | None = None,
        subgoal_of: str | None = None,
    ) -> WorkspaceGoal:
        """Build a goal from inference-layer output. Factory helper so
        callers don't have to compute the id themselves.

        ``artefact_refs`` / ``subgoal_of`` are 0.8.2 WS2 additions; omit
        them for non-artefact-backed sources.
        """

        gid = goal_id(source, title)
        return cls(
            id=gid,
            title=title,
            description=description,
            source=source,
            confidence=confidence,
            evidence=list(evidence or []),
            related_files=list(related_files or []),
            artefact_refs=list(artefact_refs or []),
            subgoal_of=subgoal_of,
        )
