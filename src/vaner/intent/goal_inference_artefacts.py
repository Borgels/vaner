# SPDX-License-Identifier: Apache-2.0
"""WS2 — intent-bearing artefacts → goal candidates.

Turns ingested :class:`IntentArtefact` records into :class:`GoalCandidate`
hints that feed :func:`vaner.intent.goal_inference.merge_hints`.

Produces two flavours of candidate:

1. **``artefact_declared``** — the artefact's title *names* the goal
   directly. One candidate per artefact. This is the strongest artefact-
   backed signal; its priority sits just below ``user_declared`` in the
   merge tiebreak.

2. **``artefact_inferred``** — the artefact's item structure *implies*
   subgoals. Outlines with ≥3 top-level sections, plans with named
   phases, and briefs that enumerate areas of focus emit one candidate
   per section/phase. These are weaker signals (priority just above
   ``commit_cluster``) and carry ``subgoal_of`` pointing at the parent
   artefact-declared goal.

Every emitted candidate carries:

- ``artefact_refs`` — the backing :class:`IntentArtefact` id.
- ``evidence`` — one ``GoalEvidence(kind="artefact_item")`` per
  unfinished/active item that supports the candidate. Reconciliation
  (WS3) updates evidence weights as item state moves.
- ``related_files`` — the union of item ``related_files``. Feeds
  scenario scoring in :mod:`vaner.intent.frontier` so predictions
  anchored to the artefact bias toward the files it references.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from vaner.intent.artefacts import IntentArtefact, IntentArtefactItem
from vaner.intent.goal_inference import GoalCandidate
from vaner.intent.goals import GoalEvidence, goal_id

# Confidence tuning — kept slightly below user_declared (1.0) so a user
# override always wins, and above commit/query clustering so an explicit
# written artefact outranks ambient signal.
DECLARED_BASE_CONFIDENCE = 0.85
INFERRED_BASE_CONFIDENCE = 0.65

# Minimum top-level sections (h2 headings) an artefact must have before
# we emit subgoal candidates. Below this, the artefact is treated as
# one atomic goal.
MIN_SECTIONS_FOR_SUBGOALS = 3

# Item states that count as "active" evidence. Complete items have
# already happened; contradicted items were explicitly retracted —
# neither supports the goal going forward.
_ACTIVE_ITEM_STATES: frozenset[str] = frozenset({"pending", "in_progress", "stalled"})


@dataclass(frozen=True, slots=True)
class ArtefactHints:
    """The bundle of candidates one artefact produces.

    ``primary`` is the ``artefact_declared`` candidate for the artefact
    itself; ``subgoals`` lists any ``artefact_inferred`` candidates for
    top-level sections. The engine merges both lists into the single
    stream consumed by :func:`merge_hints`.
    """

    primary: GoalCandidate
    subgoals: tuple[GoalCandidate, ...]

    def as_list(self) -> list[GoalCandidate]:
        return [self.primary, *self.subgoals]


def hint_from_artefact(
    artefact: IntentArtefact,
    items: list[IntentArtefactItem],
) -> ArtefactHints:
    """Produce goal candidates from one artefact.

    ``artefact`` is the stored :class:`IntentArtefact`; ``items`` is the
    flattened item list from its current snapshot. The caller
    (typically the engine's goal-loading path) is responsible for
    filtering to the artefact's *latest* snapshot items before calling
    here — this function trusts the list it's given.
    """

    primary = _primary_candidate(artefact, items)
    subgoals = tuple(_subgoal_candidates(artefact, items, parent=primary))
    return ArtefactHints(primary=primary, subgoals=subgoals)


def hints_from_artefacts(
    bundle: list[tuple[IntentArtefact, list[IntentArtefactItem]]],
) -> list[GoalCandidate]:
    """Flatten :func:`hint_from_artefact` across many artefacts.

    Convenience helper for the engine's goal-loading path — it lists
    active artefacts and their items, then passes the pairs here to
    produce a flat candidate stream ready for ``merge_hints``.
    """

    out: list[GoalCandidate] = []
    for artefact, items in bundle:
        out.extend(hint_from_artefact(artefact, items).as_list())
    return out


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _primary_candidate(
    artefact: IntentArtefact,
    items: list[IntentArtefactItem],
) -> GoalCandidate:
    """Build the ``artefact_declared`` candidate for the artefact itself.

    Evidence is drawn from active *actionable* items (tasks, subgoals,
    dependencies, notes). Section items are structural markers in the
    heading tree; they carry a nominal ``pending`` state from the
    extractor but do not constitute evidence that work is ongoing.
    """

    active = [it for it in items if it.state in _ACTIVE_ITEM_STATES and it.kind != "section"]
    evidence = tuple(
        GoalEvidence(
            kind="artefact_item",
            value=it.id,
            weight=_weight_for_item_state(it.state),
        )
        for it in active
    )
    related_files = tuple(_union_related_files(items))
    description = _primary_description(artefact, active)
    # Confidence blends the classifier's own posterior (the artefact's
    # ``confidence``) with a small boost when the artefact actually
    # carries work items rather than pure narrative.
    boost = min(0.1, 0.01 * len(active))
    confidence = min(DECLARED_BASE_CONFIDENCE + boost, 0.95) * artefact.confidence
    return GoalCandidate(
        title=artefact.title,
        source="artefact_declared",
        confidence=confidence,
        description=description,
        evidence=evidence,
        related_files=related_files,
        artefact_refs=(artefact.id,),
    )


def _subgoal_candidates(
    artefact: IntentArtefact,
    items: list[IntentArtefactItem],
    *,
    parent: GoalCandidate,
) -> list[GoalCandidate]:
    """Emit ``artefact_inferred`` candidates for top-level sections.

    "Top-level" = section items whose ``section_path`` has exactly one
    segment (i.e. first-level headings, e.g. ``## Phase 1`` under
    ``# Release``). Deeper headings feed their parent section rather
    than becoming independent subgoals, to keep the inferred goal
    population bounded.
    """

    sections = _top_level_sections(items)
    if len(sections) < MIN_SECTIONS_FOR_SUBGOALS:
        return []

    parent_id = goal_id(parent.source, parent.title)
    out: list[GoalCandidate] = []
    for section in sections:
        children = _children_of_section(section, items)
        active_children = [c for c in children if c.state in _ACTIVE_ITEM_STATES and c.kind != "section"]
        if not active_children:
            continue
        evidence = tuple(
            GoalEvidence(
                kind="artefact_item",
                value=c.id,
                weight=_weight_for_item_state(c.state),
            )
            for c in active_children
        )
        related_files = tuple(_union_related_files(children))
        description = _subgoal_description(section, active_children)
        confidence = INFERRED_BASE_CONFIDENCE * artefact.confidence
        out.append(
            GoalCandidate(
                title=f"{artefact.title}: {section.text}",
                source="artefact_inferred",
                confidence=confidence,
                description=description,
                evidence=evidence,
                related_files=related_files,
                artefact_refs=(artefact.id,),
                subgoal_of=parent_id,
            )
        )
    return out


def _top_level_sections(items: list[IntentArtefactItem]) -> list[IntentArtefactItem]:
    """Return sections at the first subgoal-worthy heading level.

    The extractor produces section paths like ``Root/Section/Subsection``
    where ``Root`` is the artefact's H1 heading. When the artefact has
    exactly one H1 (the usual case), the H2 sections underneath are the
    real subgoals — so we pick depth-2 sections. When the artefact has
    multiple depth-1 headings (no H1 wrapper, e.g. issue bodies with
    several ``## Section`` blocks), those depth-1 sections are the
    subgoals. Deeper nested sections feed their parent rather than
    becoming independent subgoals, to keep the inferred goal population
    bounded.
    """

    depth_one = [it for it in items if it.kind == "section" and _path_depth(it.section_path) == 1]
    depth_two = [it for it in items if it.kind == "section" and _path_depth(it.section_path) == 2]
    # One H1 wrapper + multiple H2s → use H2 as subgoals.
    if len(depth_one) == 1 and len(depth_two) >= 1:
        return depth_two
    # Otherwise depth-1 sections ARE the top-level subgoals (no wrapper).
    return depth_one


def _path_depth(section_path: str) -> int:
    if not section_path:
        return 0
    return len([seg for seg in section_path.split("/") if seg])


def _children_of_section(
    section: IntentArtefactItem,
    items: list[IntentArtefactItem],
) -> list[IntentArtefactItem]:
    """Items that live under ``section`` in the extractor's section-path
    hierarchy.

    A child is any item whose ``section_path`` either exactly matches
    this section's path (in which case the item is a direct bullet /
    task under the heading) or starts with ``section.section_path/``
    (in which case the item lives under a deeper nested section).
    """

    base = section.section_path
    prefix = base + "/"
    children: list[IntentArtefactItem] = []
    for it in items:
        if it.id == section.id:
            continue
        if it.section_path == base and it.kind != "section":
            children.append(it)
        elif it.section_path.startswith(prefix):
            children.append(it)
    return children


def _union_related_files(items: list[IntentArtefactItem]) -> list[str]:
    seen: OrderedDict[str, None] = OrderedDict()
    for it in items:
        for path in it.related_files:
            if path not in seen:
                seen[path] = None
    return list(seen.keys())


def _weight_for_item_state(state: str) -> float:
    if state == "in_progress":
        return 0.9
    if state == "stalled":
        return 0.5
    # pending
    return 0.7


def _primary_description(artefact: IntentArtefact, active: list) -> str:
    if not active:
        return f"Backed by intent-bearing artefact {artefact.title!r} ({artefact.kind})."
    return f"Backed by intent-bearing artefact {artefact.title!r} ({artefact.kind}) with {len(active)} active item(s)."


def _subgoal_description(section: IntentArtefactItem, active: list) -> str:
    return f"Subgoal inferred from section {section.text!r}: {len(active)} active item(s)."
