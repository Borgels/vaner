# SPDX-License-Identifier: Apache-2.0
"""WS1 — intent-bearing artefact data model (0.8.2).

Intent-bearing artefacts are documents users *write* that declare direction:
plans, outlines, task lists, briefs, roadmaps, checklists, runbooks. They are
a distinct signal class from raw evidence (source code, docs) and from
outcome signals (commits, file changes). See ``docs`` + the 0.8.2 release
spec for the broader framing.

This module owns the core types:

- :class:`IntentArtefact` — stable identity + mutable lifecycle status.
- :class:`IntentArtefactSnapshot` — content-hash-addressed versioned state.
- :class:`IntentArtefactItem` — flattened per-item structure (tasks,
  sections, subgoals, dependencies, notes). State (``pending`` / ``complete``
  / ``stalled`` / ``contradicted``) is updated by reconciliation (WS3).
- :class:`PolicyConsumerMetadata` — the stable five-field metadata block
  carried on every artefact-backed ``WorkspaceGoal`` and every
  artefact-item-anchored ``PredictionSpec``, per spec §6.6.
- :class:`ReconciliationOutcome` — persisted output of one reconciliation
  pass. The ``progress_reconciled`` invalidation signal carries only a
  pointer (``outcome_id`` + ``artefact_id``); downstream scoring and
  explanation paths read full detail from here. This keeps reconciliation
  state first-class and queryable, not ephemeral.

The dataclass shape mirrors :mod:`vaner.intent.goals` (``WorkspaceGoal``) and
:mod:`vaner.intent.prediction` (``PredictedPrompt``) so callers can treat
identity, mutable state, and evidence independently.

Cycle-safety note: this module has **no** dependency on ``goals.py`` or
``prediction.py``. Status strings that cross module boundaries
(``PolicyConsumerMetadata.status`` is either a ``GoalStatus`` or a
prediction status) are typed as plain ``str`` here. The authoritative enum
lives with the owning record type.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Literal

IntentArtefactKind = Literal[
    "plan",
    "outline",
    "task_list",
    "brief",
    "queue",
    "checklist",
    "runbook",
]

IntentArtefactStatus = Literal[
    "active",
    "dormant",
    "stale",
    "superseded",
    "archived",
]

ItemState = Literal[
    "pending",
    "in_progress",
    "complete",
    "stalled",
    "contradicted",
]

ItemKind = Literal[
    "task",
    "section",
    "subgoal",
    "dependency",
    "note",
]

SourceTier = Literal["T1", "T2", "T3", "T4"]

ReconciliationState = Literal[
    "unreconciled",
    "current",
    "drifted",
    "contradicted",
    "superseded",
]

UnfinishedItemState = Literal[
    "none",
    "pending",
    "in_progress",
    "stalled",
    "blocked",
]


def _short_hash(payload: str) -> str:
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def artefact_id(source_uri: str, kind: IntentArtefactKind) -> str:
    """Stable id for an ``IntentArtefact`` — identity key across snapshots."""
    return _short_hash(f"{source_uri}|{kind}")


def artefact_item_id(artefact_id_: str, section_path: str, text: str) -> str:
    """Stable id for an ``IntentArtefactItem``.

    Identity key = (artefact id, section path, item text). Two identical
    lines at the same section path collapse to one id intentionally so
    state survives across snapshots when content is stable.
    ``section_path`` disambiguates items whose text repeats under
    different headings (e.g. two ``- review`` bullets in different
    sections).
    """
    return _short_hash(f"{artefact_id_}|{section_path}|{text}")


def snapshot_id(content_hash: str) -> str:
    """Snapshot ids are the normalized-content hash. Identical content →
    identical snapshot → deduplicated storage."""
    return content_hash


def reconciliation_outcome_id(artefact_id_: str, pass_at: float) -> str:
    """Stable id for a ``ReconciliationOutcome``. ``pass_at`` is the
    epoch-seconds timestamp at which the pass produced the outcome."""
    return _short_hash(f"{artefact_id_}|{pass_at:.6f}")


@dataclass(frozen=True, slots=True)
class PolicyConsumerMetadata:
    """Stable five-field metadata block carried on every artefact-backed
    ``WorkspaceGoal`` and every artefact-item-anchored ``PredictionSpec``.

    Per spec §6.6, downstream scheduling / allocation / abstention /
    explanation policies read these fields directly and **must never**
    derive them from raw state. Every update path (classifier,
    goal-inference merge, reconciliation) is responsible for keeping the
    block accurate.

    - ``status`` — the record's own lifecycle state. For a
      ``WorkspaceGoal`` this is a ``GoalStatus``; for a ``PredictionSpec``
      this is the spec's own status literal. Typed as ``str`` at this
      boundary to avoid a module cycle.
    - ``confidence`` — 0.0–1.0 posterior. Reconciliation-updated, never
      wall-clock-decayed.
    - ``freshness`` — 0.0 (stale) to 1.0 (just observed). Derived from
      signal activity against ``last_observed_at``, **not** from raw
      elapsed time.
    - ``reconciliation_state`` — output of the most recent
      ``progress_reconciled`` pass that touched this record.
    - ``unfinished_item_state`` — for goals, the aggregate over their
      items; for specs, directly mirrors the anchored item.
    """

    status: str
    confidence: float
    freshness: float
    reconciliation_state: ReconciliationState
    unfinished_item_state: UnfinishedItemState

    @classmethod
    def unreconciled(
        cls,
        *,
        status: str,
        confidence: float,
        unfinished_item_state: UnfinishedItemState = "none",
    ) -> PolicyConsumerMetadata:
        """Factory for a record that has never been through reconciliation.

        Used at classifier / ingestion time, before WS3 has a chance to
        update ``reconciliation_state``. ``freshness`` starts at 1.0 —
        the record was just observed.
        """

        return cls(
            status=status,
            confidence=confidence,
            freshness=1.0,
            reconciliation_state="unreconciled",
            unfinished_item_state=unfinished_item_state,
        )


@dataclass(slots=True)
class IntentArtefactItem:
    """A flattened item extracted from an intent-bearing artefact.

    Items are the atomic units of plan structure: a task, a heading, a
    subgoal, a dependency edge, or a note. ``state`` is updated by
    reconciliation (WS3); ``evidence_refs`` accumulates the signal-event
    ids and commit SHAs that corroborated each transition.

    The dataclass is mutable so store rows and in-memory records stay in
    sync the same way :class:`WorkspaceGoal` does.
    """

    id: str
    artefact_id: str
    text: str
    kind: ItemKind
    state: ItemState
    section_path: str
    parent_item: str | None = None
    related_files: list[str] = field(default_factory=list)
    related_entities: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        *,
        artefact_id: str,
        text: str,
        kind: ItemKind,
        section_path: str,
        state: ItemState = "pending",
        parent_item: str | None = None,
        related_files: list[str] | None = None,
        related_entities: list[str] | None = None,
    ) -> IntentArtefactItem:
        """Build an item with a freshly computed stable id."""

        iid = artefact_item_id(artefact_id, section_path, text)
        return cls(
            id=iid,
            artefact_id=artefact_id,
            text=text,
            kind=kind,
            state=state,
            section_path=section_path,
            parent_item=parent_item,
            related_files=list(related_files or []),
            related_entities=list(related_entities or []),
        )


@dataclass(slots=True)
class IntentArtefactSnapshot:
    """A versioned content snapshot of an :class:`IntentArtefact`.

    Snapshots are content-hash-addressed: identical normalized content →
    identical ``id`` → one row in the store. Per-item state changes
    across snapshots are the raw input to reconciliation — comparing the
    flattened ``items`` list against the previous snapshot surfaces
    checkbox flips, new tasks, and removed sections.
    """

    id: str
    artefact_id: str
    captured_at: float
    content_hash: str
    text: str
    items: list[IntentArtefactItem] = field(default_factory=list)


@dataclass(slots=True)
class IntentArtefact:
    """Stable identity + mutable lifecycle for an intent-bearing artefact.

    - ``confidence`` — the classifier's posterior that this is actually
      intent-bearing. Distinct from per-item state confidence, which
      lives on :class:`IntentArtefactItem`.
    - ``supersedes`` — prior artefact id when reconciliation detects
      replacement (e.g. a new roadmap.md overwrites an older one).
    - ``linked_goals`` / ``linked_files`` — denormalized cache fields
      populated during ingestion + reconciliation. Store the id or path
      list the artefact influences; consumers reverse-lookup via
      ``vaner.artefacts.influence`` (§MCP tools).
    """

    id: str
    source_uri: str
    source_tier: SourceTier
    connector: str
    kind: IntentArtefactKind
    title: str
    status: IntentArtefactStatus
    confidence: float
    created_at: float = field(default_factory=time.time)
    last_observed_at: float = field(default_factory=time.time)
    last_reconciled_at: float | None = None
    latest_snapshot: str = ""
    linked_goals: list[str] = field(default_factory=list)
    linked_files: list[str] = field(default_factory=list)
    supersedes: str | None = None

    @classmethod
    def new(
        cls,
        *,
        source_uri: str,
        source_tier: SourceTier,
        connector: str,
        kind: IntentArtefactKind,
        title: str,
        confidence: float,
        status: IntentArtefactStatus = "active",
    ) -> IntentArtefact:
        """Build a fresh artefact with a stable id and active status."""

        aid = artefact_id(source_uri, kind)
        return cls(
            id=aid,
            source_uri=source_uri,
            source_tier=source_tier,
            connector=connector,
            kind=kind,
            title=title,
            status=status,
            confidence=confidence,
        )


@dataclass(frozen=True, slots=True)
class ArtefactItemStateDelta:
    """One entry in a :class:`ReconciliationOutcome` item-state delta list."""

    item_id: str
    from_state: ItemState
    to_state: ItemState


@dataclass(frozen=True, slots=True)
class GoalStatusDelta:
    """One entry in a :class:`ReconciliationOutcome` goal-status delta list.

    ``from_status`` / ``to_status`` are ``GoalStatus`` values declared in
    :mod:`vaner.intent.goals`; typed as ``str`` here to keep this module
    import-cycle-free.
    """

    goal_id: str
    from_status: str
    to_status: str


@dataclass(slots=True)
class ReconciliationOutcome:
    """Persisted output of one reconciliation pass (spec §10.3).

    The authoritative record of what reconciliation decided and why.
    Stored in the ``intent_reconciliation_outcomes`` table; ``vaner.explain``
    fetches full detail on demand. The ``progress_reconciled`` invalidation
    signal carries only ``{outcome_id, artefact_id}`` so consumers always
    resolve the same canonical record.
    """

    id: str
    artefact_id: str
    pass_at: float
    triggering_signal_id: str | None
    item_state_deltas: list[ArtefactItemStateDelta] = field(default_factory=list)
    goal_status_deltas: list[GoalStatusDelta] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
