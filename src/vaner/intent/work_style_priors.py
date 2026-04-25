# SPDX-License-Identifier: Apache-2.0
"""WS4 — Work-style intent priors (0.8.6).

Maps each :data:`vaner.setup.enums.WorkStyle` Literal value to a small
bundle of multiplicative nudges that the engine composes with its base
scoring / drafting knobs. The table lives here as immutable data: no
side effects, no lazy loading. Engine code calls
:func:`adjustments_for` once per cycle (or whenever
``config.setup.work_styles`` changes) and threads the result into the
existing call sites that already accept multipliers from
:class:`vaner.intent.deep_run_policy.DeepRunPresetSpec` and
:class:`vaner.intent.deep_run_policy.HorizonBiasSpec`.

Design notes
------------

- **Multiplicative + averaged.** Multipliers compose multiplicatively
  with preset/horizon multipliers (matches the convention in
  :mod:`vaner.intent.deep_run_policy`). When the user picks more than
  one work style, the multipliers are averaged arithmetically — explicit
  and predictable per the 0.8.6 plan, rather than geometrically.
- **"mixed" is the identity.** Every multiplier is 1.0, the drafting
  floor is 0.0, and the preferred-template tuple is empty. Engines
  running on default ``setup.work_styles == ["mixed"]`` configs see
  byte-identical behaviour to pre-WS4.
- **Conservative ranges.** Multipliers stay inside ``[0.7, 1.5]`` and
  the drafting floor inside ``[0.0, 0.6]``. The point is a small,
  explainable lookup — not a tuning monster. WS6+ benches can iterate
  on the numbers; the structure is fixed by spec §7.
- **Templates are a forward hook.** ``preferred_artefact_templates``
  is consumed by the briefing assembler when an artefact-template
  registry exists. If the named templates are not registered (current
  state — the registry lands in a later WS), the engine silently
  falls back to its existing template selection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, get_args

from vaner.setup.enums import WorkStyle


@dataclass(frozen=True, slots=True)
class IntentPriorAdjustments:
    """Multipliers/floors that nudge engine knobs toward a work style.

    All multiplier values are multiplicative on top of the engine's base
    value (:class:`DeepRunPresetSpec` already uses this convention; we
    compose with it the same way). The drafting evidence floor is a
    lower bound — the drafter takes ``max(threshold, floor)`` so a
    work style that demands stronger evidence can only raise the bar,
    never lower it.

    ``preferred_artefact_templates`` is a tuple of template ids the
    briefing assembler should prefer when ranking ties between
    otherwise-equivalent artefact templates. Templates that aren't
    registered are ignored silently.
    """

    artefact_alignment_weight_multiplier: float
    long_horizon_bonus_multiplier: float
    possible_branch_bonus_multiplier: float
    drafting_evidence_floor: float
    drafting_volatility_ceiling_multiplier: float
    preferred_artefact_templates: tuple[str, ...]


# ---------------------------------------------------------------------------
# The work-style table (spec §7). Immutable; values are the single source
# of truth for "what does each work style nudge."
# ---------------------------------------------------------------------------


WORK_STYLE_PRIORS: Final[Mapping[WorkStyle, IntentPriorAdjustments]] = {
    "writing": IntentPriorAdjustments(
        # Writing rewards continuity; let drafts ride on thinner evidence
        # but boost long-horizon (next-scene / next-chapter) hypotheses.
        artefact_alignment_weight_multiplier=1.2,
        long_horizon_bonus_multiplier=1.3,
        possible_branch_bonus_multiplier=1.0,
        drafting_evidence_floor=0.0,
        drafting_volatility_ceiling_multiplier=1.2,
        preferred_artefact_templates=("next_scene_draft", "outline_continuation"),
    ),
    "research": IntentPriorAdjustments(
        # Research wants broad branch coverage; weight possible-branch
        # exploration higher, demand stronger artefact alignment, and
        # keep drafting bar near baseline.
        artefact_alignment_weight_multiplier=1.4,
        long_horizon_bonus_multiplier=1.2,
        possible_branch_bonus_multiplier=1.4,
        drafting_evidence_floor=0.45,
        drafting_volatility_ceiling_multiplier=1.0,
        preferred_artefact_templates=("research_brief", "lit_review"),
    ),
    "planning": IntentPriorAdjustments(
        # Planning is artefact-anchored (decision memos, risk lists);
        # boost alignment hard, keep horizon balanced, demand decent
        # evidence so plans aren't wishy-washy.
        artefact_alignment_weight_multiplier=1.5,
        long_horizon_bonus_multiplier=1.2,
        possible_branch_bonus_multiplier=1.1,
        drafting_evidence_floor=0.50,
        drafting_volatility_ceiling_multiplier=0.9,
        preferred_artefact_templates=("decision_memo", "risk_list"),
    ),
    "support": IntentPriorAdjustments(
        # Support work wants to finish what's in flight (short horizon,
        # low branching); demand strong evidence so answers don't mislead.
        artefact_alignment_weight_multiplier=1.1,
        long_horizon_bonus_multiplier=0.8,
        possible_branch_bonus_multiplier=0.8,
        drafting_evidence_floor=0.55,
        drafting_volatility_ceiling_multiplier=0.8,
        preferred_artefact_templates=("ticket_reply", "troubleshooting_brief"),
    ),
    "learning": IntentPriorAdjustments(
        # Learning rewards exposure to adjacent concepts (high possible-
        # branch); drafting bar stays gentle so partials still surface.
        artefact_alignment_weight_multiplier=1.0,
        long_horizon_bonus_multiplier=1.1,
        possible_branch_bonus_multiplier=1.5,
        drafting_evidence_floor=0.0,
        drafting_volatility_ceiling_multiplier=1.3,
        preferred_artefact_templates=("study_outline", "concept_brief"),
    ),
    "coding": IntentPriorAdjustments(
        # Coding wants clean patches: high evidence floor, tight volatility
        # ceiling, modest long-horizon (debugging is short-loop).
        artefact_alignment_weight_multiplier=1.3,
        long_horizon_bonus_multiplier=0.9,
        possible_branch_bonus_multiplier=1.1,
        drafting_evidence_floor=0.55,
        drafting_volatility_ceiling_multiplier=0.8,
        preferred_artefact_templates=("debugging_brief", "patch_proposal"),
    ),
    "general": IntentPriorAdjustments(
        # General-purpose: very gentle nudges, no template preference.
        artefact_alignment_weight_multiplier=1.1,
        long_horizon_bonus_multiplier=1.0,
        possible_branch_bonus_multiplier=1.1,
        drafting_evidence_floor=0.0,
        drafting_volatility_ceiling_multiplier=1.0,
        preferred_artefact_templates=(),
    ),
    "mixed": IntentPriorAdjustments(
        # Identity element. ``setup.work_styles == ["mixed"]`` is the
        # default for fresh installs; it MUST be a strict no-op so the
        # engine produces byte-identical behaviour to pre-WS4.
        artefact_alignment_weight_multiplier=1.0,
        long_horizon_bonus_multiplier=1.0,
        possible_branch_bonus_multiplier=1.0,
        drafting_evidence_floor=0.0,
        drafting_volatility_ceiling_multiplier=1.0,
        preferred_artefact_templates=(),
    ),
    "unsure": IntentPriorAdjustments(
        # Same identity as ``mixed`` — the user opted out of telling us;
        # we should not gamble on a guess. Functionally equivalent today;
        # split out so future surfaces can show "unsure" as a distinct
        # affordance without breaking the table key invariant.
        artefact_alignment_weight_multiplier=1.0,
        long_horizon_bonus_multiplier=1.0,
        possible_branch_bonus_multiplier=1.0,
        drafting_evidence_floor=0.0,
        drafting_volatility_ceiling_multiplier=1.0,
        preferred_artefact_templates=(),
    ),
}


# ---------------------------------------------------------------------------
# Lookups + helpers
# ---------------------------------------------------------------------------


def default_adjustments() -> IntentPriorAdjustments:
    """Return the identity-element adjustments (``mixed``).

    Used when ``setup.work_styles`` is empty or contains only the
    ``"mixed"`` sentinel. Every multiplier is 1.0, the drafting floor
    is 0.0, and the preferred-template tuple is empty — composing
    these with any base value yields the base value unchanged.
    """

    return WORK_STYLE_PRIORS["mixed"]


def adjustments_for(work_styles: Sequence[WorkStyle]) -> IntentPriorAdjustments:
    """Average the per-style adjustments across a multi-select input.

    Empty input falls back to :func:`default_adjustments` (the
    ``"mixed"`` identity element). When more than one style is
    selected, the float multipliers are averaged arithmetically;
    template tuples are unioned (deduplicated, sorted) so the result
    is deterministic regardless of input order.

    Unknown style names raise :class:`KeyError` — that would be a
    schema-migration bug, not a runtime concern.
    """

    if not work_styles:
        return default_adjustments()

    specs = [WORK_STYLE_PRIORS[style] for style in work_styles]
    n = len(specs)

    templates: set[str] = set()
    for spec in specs:
        templates.update(spec.preferred_artefact_templates)

    return IntentPriorAdjustments(
        artefact_alignment_weight_multiplier=sum(s.artefact_alignment_weight_multiplier for s in specs) / n,
        long_horizon_bonus_multiplier=sum(s.long_horizon_bonus_multiplier for s in specs) / n,
        possible_branch_bonus_multiplier=sum(s.possible_branch_bonus_multiplier for s in specs) / n,
        drafting_evidence_floor=sum(s.drafting_evidence_floor for s in specs) / n,
        drafting_volatility_ceiling_multiplier=sum(s.drafting_volatility_ceiling_multiplier for s in specs) / n,
        preferred_artefact_templates=tuple(sorted(templates)),
    )


def compose_with_deep_run_preset(
    work_style: IntentPriorAdjustments,
    *,
    base_artefact_alignment: float,
    base_long_horizon: float,
    base_possible_branch: float,
) -> tuple[float, float, float]:
    """Multiply work-style multipliers onto base scoring weights.

    The composition is multiplicative — the same convention used by
    :class:`vaner.intent.deep_run_policy.HorizonBiasSpec` so that
    work-style + Deep-Run preset + horizon bias compose associatively
    at the call site (any order yields the same product).

    Returns the three adjusted scoring weights as a tuple in the same
    order as the parameters: artefact-alignment, long-horizon,
    possible-branch.
    """

    return (
        base_artefact_alignment * work_style.artefact_alignment_weight_multiplier,
        base_long_horizon * work_style.long_horizon_bonus_multiplier,
        base_possible_branch * work_style.possible_branch_bonus_multiplier,
    )


# Self-check: every WorkStyle Literal value must have an entry in
# WORK_STYLE_PRIORS. We also expose the literal's args for the test
# module to consume without re-importing typing internals.
_WORK_STYLE_VALUES: Final[tuple[WorkStyle, ...]] = get_args(WorkStyle)
assert set(_WORK_STYLE_VALUES) == set(WORK_STYLE_PRIORS.keys()), (
    "WORK_STYLE_PRIORS must cover every value in the WorkStyle Literal "
    f"(missing: {set(_WORK_STYLE_VALUES) - set(WORK_STYLE_PRIORS.keys())}, "
    f"extra: {set(WORK_STYLE_PRIORS.keys()) - set(_WORK_STYLE_VALUES)})"
)


__all__ = [
    "WORK_STYLE_PRIORS",
    "IntentPriorAdjustments",
    "adjustments_for",
    "compose_with_deep_run_preset",
    "default_adjustments",
]
