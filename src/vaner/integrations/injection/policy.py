# SPDX-License-Identifier: Apache-2.0
"""Decide whether to inject prepared material, and in what form.

The decision is intentionally pure-data — it takes a snapshot of the
current injection state and returns a :class:`InjectionDecision`. The MCP
and HTTP layers then act on that decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from vaner.integrations.injection.mode import ContextInjectionMode


@dataclass(frozen=True)
class InjectionInputs:
    """Snapshot of runtime state used to decide injection."""

    mode: ContextInjectionMode
    has_fresh_adopted_package_in_context: bool = False
    """Whether the current prompt already carries a fresh `<VANER_ADOPTED_PACKAGE>`."""

    has_active_predictions: bool = False
    """Whether the prediction registry has any non-stale entries."""

    top_prediction_is_ready: bool = False
    """Whether the top-ranked prediction is in state `ready` or `drafting`."""

    top_prediction_is_fresh: bool = False
    """Whether the top-ranked prediction's freshness invariants still hold."""

    top_prediction_confidence: float = 0.0
    """Highest-ranked prediction's compute_contract confidence, 0..1."""

    current_context_used_fraction: float = 0.0
    """Fraction (0..1) of the active context window already consumed."""

    max_context_fraction: float = 0.20
    """Hard ceiling on what fraction of remaining context Vaner may consume."""

    query_is_vaner_relevant: bool = True
    """Heuristic signal from the relevance layer. False → nothing to inject for this turn."""


@dataclass(frozen=True)
class InjectionDecision:
    """Outcome of :func:`should_inject`."""

    emit_digest: bool = False
    emit_adopted_package: bool = False
    suppressed_reason: str | None = None
    """Human-readable reason when both emit_* are False."""


_TOP_MATCH_CONFIDENCE_FLOOR = 0.75


def should_inject(inputs: InjectionInputs) -> InjectionDecision:
    """Return the decision for *inputs* given its mode."""
    # Global short-circuits — apply to every mode.
    if inputs.current_context_used_fraction >= (1.0 - inputs.max_context_fraction):
        return InjectionDecision(suppressed_reason="context_budget_exhausted")
    if not inputs.query_is_vaner_relevant:
        return InjectionDecision(suppressed_reason="irrelevant_for_turn")

    if inputs.has_fresh_adopted_package_in_context:
        # The model already has the full package; anything we add is redundant
        # and risks double-counting the evidence.
        return InjectionDecision(suppressed_reason="fresh_adopted_package_present")

    mode = inputs.mode

    if mode is ContextInjectionMode.NONE:
        return InjectionDecision(suppressed_reason="mode_none")

    if mode is ContextInjectionMode.CLIENT_CONTROLLED:
        # Tier-4 clients (MCP Apps UI) assemble context themselves.
        return InjectionDecision(suppressed_reason="client_controlled")

    if mode is ContextInjectionMode.DIGEST_ONLY:
        if not inputs.has_active_predictions:
            return InjectionDecision(suppressed_reason="no_active_predictions")
        return InjectionDecision(emit_digest=True)

    if mode is ContextInjectionMode.ADOPTED_PACKAGE_ONLY:
        # This mode assumes an adopted package was stashed server-side but is
        # not yet in the prompt. If nothing is adoptable/fresh, skip.
        if not (inputs.top_prediction_is_ready and inputs.top_prediction_is_fresh):
            return InjectionDecision(suppressed_reason="no_fresh_adopted_package")
        return InjectionDecision(emit_adopted_package=True)

    if mode is ContextInjectionMode.TOP_MATCH_AUTO_INCLUDE:
        if not inputs.has_active_predictions:
            return InjectionDecision(suppressed_reason="no_active_predictions")
        if not (inputs.top_prediction_is_ready and inputs.top_prediction_is_fresh):
            return InjectionDecision(suppressed_reason="top_match_not_ready_or_stale")
        if inputs.top_prediction_confidence < _TOP_MATCH_CONFIDENCE_FLOOR:
            return InjectionDecision(suppressed_reason="top_match_confidence_low")
        return InjectionDecision(emit_adopted_package=True)

    if mode is ContextInjectionMode.POLICY_HYBRID:
        if not inputs.has_active_predictions:
            return InjectionDecision(suppressed_reason="no_active_predictions")
        # Prefer the stronger signal (adopted package) when the user has
        # already reached 'ready'; otherwise fall back to the digest.
        if (
            inputs.top_prediction_is_ready
            and inputs.top_prediction_is_fresh
            and inputs.top_prediction_confidence >= _TOP_MATCH_CONFIDENCE_FLOOR
        ):
            return InjectionDecision(emit_adopted_package=True)
        return InjectionDecision(emit_digest=True)

    return InjectionDecision(suppressed_reason=f"unknown_mode:{mode}")
