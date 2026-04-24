# SPDX-License-Identifier: Apache-2.0
"""WS2 — unified goal-inference pipeline (0.8.2).

Consumes :class:`GoalCandidate` records from every goal-inference source
(branch names, commit clusters, query clusters, intent-bearing artefacts)
and produces the deduplicated, evidence-merged set of
:class:`WorkspaceGoal` records the engine uses to seed predictions.

Replaces the pre-0.8.2 single-source path that lived in
``engine._load_active_goals``. Each per-source producer emits its own
``GoalCandidate`` list; this module handles the cross-source merge,
title normalization, evidence union, and primary-source selection.

Design notes
------------

- **Semantic identity** — candidates with the same normalized title
  collapse to one goal. Normalization is lowercase + trimmed + collapsed
  whitespace. Richer semantic matching (embeddings) is deferred to a
  later iteration; the fixture corpus and per-source producers emit
  normalized titles to avoid relying on it.

- **Source priority** — when multiple sources produce the same goal we
  pick the highest-tier source as the "primary" and use its identity
  (``goal_id(primary.source, primary.title)``). Evidence from all
  sources is merged into the goal's evidence list. Priority order:
  ``user_declared > artefact_declared > branch_name > artefact_inferred
  > commit_cluster > query_cluster``.

- **Confidence blending** — the merged goal's confidence takes the
  primary source's confidence as a floor, then applies a small corroboration
  bonus (+0.05 per additional source, capped at 1.0). Agreement across
  independent signals is meaningful; we just don't let it overrule the
  primary's own posterior.

- **Stability across cycles** — when the primary source disappears in a
  later cycle (e.g. branch changed) but another source still supports
  the goal, the goal's id changes because it's keyed on the primary
  source. This is intentional — the identity reflects the strongest
  current evidence. Downstream consumers that need stable titles across
  source transitions can de-dupe on ``title`` rather than ``id``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from vaner.intent.goals import (
    GoalEvidence,
    GoalSource,
    GoalStatus,
    WorkspaceGoal,
    goal_id,
)

# Higher number wins. Keep user_declared on top — it's authoritative.
_SOURCE_PRIORITY: dict[GoalSource, int] = {
    "user_declared": 100,
    "artefact_declared": 80,
    "branch_name": 70,
    "artefact_inferred": 60,
    "commit_cluster": 50,
    "query_cluster": 40,
}

_CORROBORATION_BONUS_PER_SOURCE = 0.05
_MAX_CORROBORATED_CONFIDENCE = 1.0

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class GoalCandidate:
    """Normalized proposal from one of the goal-inference sources.

    Every per-source producer (``branch_parser.parse_branch_name``,
    ``goal_inference_commits.cluster_commit_subjects``,
    ``goal_inference_queries.cluster_query_history``,
    ``goal_inference_artefacts.hint_from_artefact``) emits these. The
    :func:`merge_hints` coordinator consumes the list and returns the
    deduplicated set of :class:`WorkspaceGoal` records.

    - ``title`` — human-readable goal title. Normalized for dedupe; the
      original casing is preserved for display when the candidate wins
      the source-priority tiebreak.
    - ``description`` — optional long form. Empty string when only the
      title was inferred (matches pre-0.8.2 ``WorkspaceGoal`` behaviour).
    - ``source`` — the emitting source. Drives the priority tiebreak.
    - ``confidence`` — 0.0–1.0 posterior from the source.
    - ``evidence`` — supporting ``GoalEvidence`` records. For artefact
      sources this includes one ``artefact_item`` entry per backing item.
    - ``related_files`` — file-path hints that flow into scenario
      scoring.
    - ``artefact_refs`` — ``IntentArtefact`` ids backing this candidate;
      non-empty only for ``artefact_declared`` / ``artefact_inferred``.
    - ``subgoal_of`` — parent goal id when this candidate is a
      decomposition (e.g. a subgoal extracted from an outline item).
    """

    title: str
    source: GoalSource
    confidence: float
    description: str = ""
    evidence: tuple[GoalEvidence, ...] = ()
    related_files: tuple[str, ...] = ()
    artefact_refs: tuple[str, ...] = ()
    subgoal_of: str | None = None


def normalize_title(title: str) -> str:
    """Lowercase + trim + collapse whitespace. The dedupe key."""

    if not title:
        return ""
    collapsed = _WHITESPACE_RE.sub(" ", title.strip())
    return collapsed.lower()


def merge_hints(
    candidates: list[GoalCandidate],
    *,
    default_status: GoalStatus = "active",
) -> list[WorkspaceGoal]:
    """Deduplicate + merge candidates into a list of :class:`WorkspaceGoal`.

    Candidates with the same normalized title collapse to one goal. The
    highest-tier source by :data:`_SOURCE_PRIORITY` provides the goal's
    identity (``goal_id(source, title)``), display title, and
    description. Evidence and related-file sets union across sources;
    confidence takes the primary's value as a floor and adds a small
    corroboration bonus per additional backing source.

    Returns an empty list when ``candidates`` is empty. Preserves
    insertion order of the first time each normalized title was seen so
    downstream consumers get stable iteration.
    """

    if not candidates:
        return []

    groups: dict[str, list[GoalCandidate]] = {}
    order: list[str] = []
    for cand in candidates:
        if cand.confidence <= 0:
            continue
        key = normalize_title(cand.title)
        if not key:
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(cand)

    merged: list[WorkspaceGoal] = []
    for key in order:
        group = groups[key]
        primary = _pick_primary(group)
        evidence = _union_evidence(group)
        related_files = _union_strings(c.related_files for c in group)
        artefact_refs = _union_strings(c.artefact_refs for c in group)
        confidence = _blend_confidence(group, primary.confidence)
        subgoal_of = _pick_subgoal_parent(group)

        goal = WorkspaceGoal.from_hint(
            title=primary.title,
            source=primary.source,
            confidence=confidence,
            description=primary.description,
            evidence=list(evidence),
            related_files=related_files,
            artefact_refs=artefact_refs,
            subgoal_of=subgoal_of,
        )
        goal.status = default_status
        merged.append(goal)
    return merged


# -------------------------------------------------------------------------
# Internals
# -------------------------------------------------------------------------


def _pick_primary(group: list[GoalCandidate]) -> GoalCandidate:
    """Pick the highest-priority candidate in the group. Ties broken by
    confidence, then insertion order."""

    return max(
        group,
        key=lambda c: (_SOURCE_PRIORITY.get(c.source, 0), c.confidence),
    )


def _union_evidence(group: list[GoalCandidate]) -> list[GoalEvidence]:
    """Union evidence across the group, deduplicating by ``(kind, value)``.

    When the same piece of evidence is emitted by multiple sources, the
    highest weight wins.
    """

    pool: dict[tuple[str, str], GoalEvidence] = {}
    for cand in group:
        for ev in cand.evidence:
            key = (ev.kind, ev.value)
            existing = pool.get(key)
            if existing is None or ev.weight > existing.weight:
                pool[key] = ev
    return list(pool.values())


def _union_strings(value_iters) -> list[str]:
    """Ordered de-dupe across one or more iterables."""

    seen: list[str] = []
    for values in value_iters:
        for v in values:
            if v and v not in seen:
                seen.append(v)
    return seen


def _blend_confidence(group: list[GoalCandidate], base: float) -> float:
    """Start from the primary's confidence, add a corroboration bonus for
    every additional distinct source. Caps at 1.0."""

    distinct_sources = {c.source for c in group}
    bonus = _CORROBORATION_BONUS_PER_SOURCE * max(0, len(distinct_sources) - 1)
    return min(_MAX_CORROBORATED_CONFIDENCE, base + bonus)


def _pick_subgoal_parent(group: list[GoalCandidate]) -> str | None:
    """Pick a parent goal id when one was proposed.

    If multiple candidates carry different parents, prefer the one from
    the highest-priority source to avoid flip-flopping across cycles.
    """

    candidates_with_parents = [c for c in group if c.subgoal_of]
    if not candidates_with_parents:
        return None
    winner = max(
        candidates_with_parents,
        key=lambda c: (_SOURCE_PRIORITY.get(c.source, 0), c.confidence),
    )
    return winner.subgoal_of


# -------------------------------------------------------------------------
# Convenience — expose the identity helper so callers that already hold a
# GoalCandidate don't need to import goals.goal_id themselves.
# -------------------------------------------------------------------------


def candidate_goal_id(candidate: GoalCandidate) -> str:
    """Identity key a candidate would upsert under if it won the merge."""

    return goal_id(candidate.source, candidate.title)


__all__ = [
    "GoalCandidate",
    "candidate_goal_id",
    "merge_hints",
    "normalize_title",
]

# Re-exports so callers can import everything they need from one module.
_re_exports: Literal["re-exports"] = "re-exports"  # sentinel for readability
del _re_exports
