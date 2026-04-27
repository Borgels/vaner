# SPDX-License-Identifier: Apache-2.0
"""Pure resolver: ``(MemoryBudget, work_styles) -> RecommendedModel``.

Used by:

* ``vaner.setup.apply._apply_to_config`` (WS10.3) — when a fresh
  install has no ``exploration_model`` configured, the resolver
  picks one from the registry.
* The HTTP / MCP endpoint at ``GET /models/recommended`` (WS10.4) —
  surfaces the recommendation to the desktop wizard's preset card.

Resolution rules (deterministic, total over the registry):

1. **Filter** to models that fit the budget
   (``min_effective_gb_q4 <= budget.effective_gb_q4``).
2. **Score** each candidate. Higher is better. The score is a
   triple ``(intent_match, params_b, -popularity_rank)`` ordered
   lexicographically:

   - ``intent_match`` is the count of overlap between the user's
     work styles and the model's ``intent_lean`` tuple (after
     collapsing ``general`` / ``unsure`` onto ``mixed``).
   - ``params_b`` rewards larger models (capabilities scale with
     parameter count, modulo runtime cost the budget already
     captured).
   - ``-popularity_rank`` rewards models the source ranks higher
     (lower rank number = more popular).

3. **Pick** the top-scoring model. Ties are broken by ``id`` for
   deterministic output across daemon restarts.

When no model fits the budget, the resolver returns ``None`` and the
caller falls back to the existing ``ExplorationConfig`` auto-detect
path.
"""

from __future__ import annotations

from collections.abc import Iterable

from vaner.setup.memory_budget import MemoryBudget
from vaner.setup.recommended.schema import IntentLean, RecommendedModel, Registry

# Map WorkStyle slugs (the wizard submits these) to the routing-relevant
# IntentLean slug. ``general`` / ``unsure`` collapse to ``mixed`` because
# they have no opinionated affinity.
_WORK_STYLE_TO_INTENT: dict[str, IntentLean] = {
    "coding": "coding",
    "writing": "writing",
    "research": "research",
    "planning": "planning",
    "support": "support",
    "learning": "learning",
    "general": "mixed",
    "mixed": "mixed",
    "unsure": "mixed",
}


def _normalise_work_styles(work_styles: Iterable[str]) -> set[IntentLean]:
    """Collapse the wizard's WorkStyle set into routing-relevant intents."""
    out: set[IntentLean] = set()
    for ws in work_styles:
        intent = _WORK_STYLE_TO_INTENT.get(ws)
        if intent is not None:
            out.add(intent)
    return out


def _score(
    model: RecommendedModel,
    intents: set[IntentLean],
) -> tuple[int, float, int]:
    """Lexicographic score: (intent_match, params_b, -popularity_rank)."""
    intent_match = sum(1 for lean in model.intent_lean if lean in intents)
    return (intent_match, model.params_b, -model.popularity_rank)


def pick_for(
    registry: Registry,
    budget: MemoryBudget,
    work_styles: Iterable[str] = (),
) -> RecommendedModel | None:
    """Return the best-fitting model for the given budget and intents.

    Pure function — no I/O, no clocks, no randomness. Same inputs
    always yield the same output.
    """
    if not registry.models or budget.effective_gb_q4 <= 0:
        return None
    intents = _normalise_work_styles(work_styles)
    fitting = [m for m in registry.models if m.min_effective_gb_q4 <= budget.effective_gb_q4]
    if not fitting:
        return None
    # Sort descending by score, then ascending by id for tie-break.
    fitting.sort(key=lambda m: (_score(m, intents), tuple(-ord(c) for c in m.id)), reverse=True)
    return fitting[0]


def alternatives_for(
    registry: Registry,
    budget: MemoryBudget,
    work_styles: Iterable[str] = (),
    *,
    limit: int = 3,
) -> tuple[RecommendedModel, ...]:
    """Return the top ``limit`` models in score order, including the pick.

    The HTTP / MCP endpoint surfaces these so the desktop wizard can
    render an "alternatives" list under the primary recommendation.
    """
    if not registry.models or budget.effective_gb_q4 <= 0 or limit <= 0:
        return ()
    intents = _normalise_work_styles(work_styles)
    fitting = [m for m in registry.models if m.min_effective_gb_q4 <= budget.effective_gb_q4]
    fitting.sort(key=lambda m: (_score(m, intents), tuple(-ord(c) for c in m.id)), reverse=True)
    return tuple(fitting[:limit])


__all__ = [
    "alternatives_for",
    "pick_for",
]
