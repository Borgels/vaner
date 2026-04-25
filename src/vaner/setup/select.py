# SPDX-License-Identifier: Apache-2.0
"""WS3 — Setup primitives: policy-bundle selection (0.8.6).

Pure ``filter -> score -> pick`` pipeline that maps a
:class:`SetupAnswers` plus a :class:`HardwareProfile` snapshot to one of
the seven bundles in :data:`vaner.setup.catalog.PROFILE_CATALOG` (spec
§10). The function is deterministic by construction — no I/O, no
clocks, no randomness, no nondeterministic dict iteration. Same input
always produces a byte-identical :class:`SelectionResult`, including
the ``reasons`` tuple and the ``runner_ups`` ordering.

Design notes:

- **Filter** drops bundles that would violate hard user posture (e.g.
  the user said *local only* and the bundle uses a cloud-preferred
  posture) or hard hardware limits (e.g. ``unknown`` tier means we
  cannot promise large-local will work, so we don't recommend it).
  Each filter is a small named callable so it can be unit-tested in
  isolation.
- **Score** runs six small ``_*_match`` callables, each returning a
  ``(score, reason)`` pair in ``[-1.0, 1.0]``. Total range across the
  six is roughly ``[-6, +6]``; ties break alphabetically on bundle id
  for determinism.
- **Forced fallback** — if filters empty the candidate set, we re-run
  with priority + hardware filters relaxed (cloud posture is the one
  hard-line the user explicitly stated). If even that produces
  nothing, we return :data:`local_lightweight` as the safest possible
  default and flag ``forced_fallback=True``.

The match weights are intentionally small integer-ish floats: the goal
is for the *filter* to do most of the work and the *score* to break
the remaining ties in a way the user can read in the disclosure
panel. We deliberately avoid neural / continuous knobs here — the
user-facing reasons must remain explainable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from vaner.setup.answers import SetupAnswers
from vaner.setup.catalog import PROFILE_CATALOG, bundle_by_id
from vaner.setup.enums import (
    BackgroundPosture,
    CloudPosture,
    ComputePosture,
    HardwareTier,
    Priority,
    WorkStyle,
)
from vaner.setup.hardware import HardwareProfile
from vaner.setup.policy import VanerPolicyBundle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """Output of :func:`select_policy_bundle`.

    - ``bundle`` — the chosen :class:`VanerPolicyBundle`.
    - ``score`` — aggregate match score for the chosen bundle. Ranges
      roughly across ``[-6.0, +6.0]``; values are diagnostic only and
      should not be exposed verbatim to end users.
    - ``reasons`` — short human-readable strings explaining *why* this
      bundle won. Empty tuple means we hit the forced-fallback path.
    - ``runner_ups`` — up to two next-best bundles in score order,
      excluding the chosen one. Drives the desktop disclosure panel.
    - ``forced_fallback`` — ``True`` only when the filters emptied the
      candidate set entirely and we returned the safe default. The
      desktop UI should surface this as a warning.
    """

    bundle: VanerPolicyBundle
    score: float
    reasons: tuple[str, ...]
    runner_ups: tuple[VanerPolicyBundle, ...]
    forced_fallback: bool


# ---------------------------------------------------------------------------
# Filters — each takes (answers, hardware, bundle) and returns either
# None (keep) or a string explaining why the bundle was dropped.
# ---------------------------------------------------------------------------


_FilterFn = Callable[
    [SetupAnswers, HardwareProfile, VanerPolicyBundle],
    str | None,
]


def _filter_cloud_posture(
    answers: SetupAnswers,
    _hardware: HardwareProfile,
    bundle: VanerPolicyBundle,
) -> str | None:
    """Drop bundles that violate the user's stated cloud posture."""
    cloud = answers.cloud_posture
    posture = bundle.local_cloud_posture
    if cloud == "local_only" and posture in ("hybrid", "cloud_preferred"):
        return f"{bundle.id} uses cloud ({posture}); user requested local_only"
    if cloud == "ask_first" and posture == "cloud_preferred":
        return f"{bundle.id} prefers cloud; user said ask_first"
    return None


def _filter_priority_privacy(
    answers: SetupAnswers,
    _hardware: HardwareProfile,
    bundle: VanerPolicyBundle,
) -> str | None:
    """Drop bundles that expose data more loosely than ``privacy``."""
    if answers.priority != "privacy":
        return None
    # Privacy-conscious users get strict-or-standard floors only. The
    # current catalogue ships no "relaxed" bundles, but we keep the
    # filter so future additions (e.g. a "cloud-first" preset) cannot
    # silently match a privacy-priority user.
    if bundle.privacy_profile == "relaxed":
        return f"{bundle.id} is privacy=relaxed; user prioritised privacy"
    # "Cloud OK" interpretation — bundles whose cloud posture is the
    # cloud-preferred bucket are also dropped under priority=privacy.
    if bundle.local_cloud_posture == "cloud_preferred":
        return f"{bundle.id} prefers cloud; user prioritised privacy"
    return None


def _filter_priority_low_resource(
    answers: SetupAnswers,
    _hardware: HardwareProfile,
    bundle: VanerPolicyBundle,
) -> str | None:
    """Drop heavy-local bundles when the user said low_resource."""
    if answers.priority != "low_resource":
        return None
    if bundle.runtime_profile == "large" and bundle.local_cloud_posture in (
        "local_only",
        "local_preferred",
    ):
        return f"{bundle.id} is heavy local; user prioritised low_resource"
    return None


def _filter_hardware_tier(
    _answers: SetupAnswers,
    hardware: HardwareProfile,
    bundle: VanerPolicyBundle,
) -> str | None:
    """Drop bundles the device cannot reasonably run.

    - ``light`` hardware — drop ``runtime_profile=large`` regardless of
      posture. A small laptop should not promise local 30B-plus.
    - ``unknown`` tier — drop ``runtime_profile=large`` (we cannot
      promise it works) and ``local_cloud_posture=local_only`` (we
      cannot promise local works at all without detection); steer the
      user toward cloud-recommended defaults.
    """
    tier = hardware.tier
    if tier == "light" and bundle.runtime_profile == "large":
        return f"{bundle.id} needs large runtime; hardware tier is light"
    if tier == "unknown":
        if bundle.runtime_profile == "large":
            return f"{bundle.id} needs large runtime; hardware tier unknown"
        if bundle.local_cloud_posture == "local_only":
            return f"{bundle.id} is local_only; hardware tier unknown"
    return None


def _filter_battery_cost(
    answers: SetupAnswers,
    hardware: HardwareProfile,
    bundle: VanerPolicyBundle,
) -> str | None:
    """On battery + cost priority, drop cloud-spending bundles."""
    if answers.priority != "cost":
        return None
    if not hardware.is_battery:
        return None
    if hardware.tier != "light":
        return None
    if bundle.spend_profile in ("medium", "high"):
        return f"{bundle.id} spends on cloud; user is on battery + cost"
    return None


# Order matters only for filter dropped-reason output; logic is
# commutative. Tuple is immutable so the iteration order is fixed.
_FILTERS: tuple[_FilterFn, ...] = (
    _filter_cloud_posture,
    _filter_priority_privacy,
    _filter_priority_low_resource,
    _filter_hardware_tier,
    _filter_battery_cost,
)


def _apply_filters(
    answers: SetupAnswers,
    hardware: HardwareProfile,
    candidates: Iterable[VanerPolicyBundle],
    filters: tuple[_FilterFn, ...] = _FILTERS,
) -> tuple[tuple[VanerPolicyBundle, ...], tuple[tuple[str, str], ...]]:
    """Run all filters; return (kept, dropped_with_reason).

    ``dropped_with_reason`` is a tuple of ``(bundle_id, reason)`` pairs
    so callers (mostly tests) can introspect which filters fired.
    """
    kept: list[VanerPolicyBundle] = []
    dropped: list[tuple[str, str]] = []
    for bundle in candidates:
        drop_reason: str | None = None
        for fn in filters:
            reason = fn(answers, hardware, bundle)
            if reason is not None:
                drop_reason = reason
                break
        if drop_reason is None:
            kept.append(bundle)
        else:
            dropped.append((bundle.id, drop_reason))
    return tuple(kept), tuple(dropped)


# ---------------------------------------------------------------------------
# Match functions — each returns (score in [-1.0, 1.0], reason)
# ---------------------------------------------------------------------------


def _priority_match(priority: Priority, bundle: VanerPolicyBundle) -> tuple[float, str]:
    """Match the user's top-level priority against the bundle profile."""
    if priority == "speed":
        if bundle.latency_profile == "snappy":
            return 1.0, f"Speed-first → {bundle.id} is snappy"
        if bundle.latency_profile == "balanced":
            return 0.3, f"Speed-first → {bundle.id} is balanced"
        return -0.5, f"Speed-first → {bundle.id} prioritises quality"
    if priority == "quality":
        if bundle.latency_profile == "quality":
            return 1.0, f"Quality-first → {bundle.id} prioritises quality"
        if bundle.latency_profile == "balanced":
            return 0.2, f"Quality-first → {bundle.id} is balanced"
        return -0.3, f"Quality-first → {bundle.id} is snappy"
    if priority == "privacy":
        if bundle.privacy_profile == "strict":
            return 1.0, f"Privacy-first → {bundle.id} is strict"
        if bundle.privacy_profile == "standard":
            return 0.2, f"Privacy-first → {bundle.id} is standard"
        return -1.0, f"Privacy-first → {bundle.id} is relaxed"
    if priority == "cost":
        if bundle.spend_profile == "zero":
            return 1.0, f"Cost-first → {bundle.id} spends nothing"
        if bundle.spend_profile == "low":
            return 0.5, f"Cost-first → {bundle.id} spends little"
        return -0.5, f"Cost-first → {bundle.id} spends mid/high"
    if priority == "low_resource":
        if bundle.runtime_profile == "small":
            return 1.0, f"Low-resource → {bundle.id} runs small"
        if bundle.runtime_profile == "medium":
            return 0.0, f"Low-resource → {bundle.id} runs medium"
        return -0.7, f"Low-resource → {bundle.id} runs large"
    # priority == "balanced"
    if bundle.id == "hybrid_balanced":
        return 1.0, "Balanced priority → hybrid_balanced is the canonical default"
    if bundle.latency_profile == "balanced":
        return 0.4, f"Balanced priority → {bundle.id} is balanced"
    return 0.0, f"Balanced priority → {bundle.id} is acceptable"


def _hardware_match(tier: HardwareTier, bundle: VanerPolicyBundle) -> tuple[float, str]:
    """Map hardware tier to runtime profile preference."""
    if tier == "high_performance":
        if bundle.runtime_profile == "large":
            return 1.0, f"High-performance hardware → {bundle.id} runs large"
        if bundle.runtime_profile == "medium":
            return 0.2, f"High-performance hardware → {bundle.id} runs medium"
        return -0.4, f"High-performance hardware → {bundle.id} runs small"
    if tier == "capable":
        if bundle.runtime_profile == "medium":
            return 1.0, f"Capable hardware → {bundle.id} runs medium"
        if bundle.runtime_profile == "small":
            return 0.3, f"Capable hardware → {bundle.id} runs small"
        return -0.2, f"Capable hardware → {bundle.id} runs large"
    if tier == "light":
        if bundle.runtime_profile == "small":
            return 1.0, f"Light hardware → {bundle.id} runs small"
        if bundle.runtime_profile == "medium":
            return -0.3, f"Light hardware → {bundle.id} runs medium"
        return -1.0, f"Light hardware → {bundle.id} runs large"
    # tier == "unknown"
    if bundle.local_cloud_posture in ("hybrid", "cloud_preferred"):
        return 0.6, f"Unknown hardware → {bundle.id} can lean on cloud"
    if bundle.runtime_profile == "small":
        return 0.2, f"Unknown hardware → {bundle.id} stays modest"
    return -0.2, f"Unknown hardware → {bundle.id} assumes more than we know"


_WORKSTYLE_BUNDLE_AFFINITY: dict[WorkStyle, dict[str, float]] = {
    "writing": {"deep_research": 1.0, "hybrid_quality": 0.6, "local_balanced": 0.2},
    "research": {"deep_research": 1.0, "hybrid_quality": 0.6, "local_heavy": 0.4},
    "planning": {"deep_research": 0.8, "hybrid_balanced": 0.4, "local_balanced": 0.4},
    "support": {"hybrid_balanced": 0.6, "local_balanced": 0.4, "cost_saver": 0.4},
    "learning": {"local_balanced": 0.6, "hybrid_balanced": 0.4, "cost_saver": 0.2},
    "coding": {"local_balanced": 0.8, "hybrid_quality": 0.4, "local_heavy": 0.4},
    "general": {"hybrid_balanced": 0.6, "local_balanced": 0.4},
    "mixed": {"hybrid_balanced": 0.6, "local_balanced": 0.4},
    "unsure": {"hybrid_balanced": 0.8, "local_balanced": 0.2},
}


def _workstyle_match(
    work_styles: tuple[WorkStyle, ...],
    bundle: VanerPolicyBundle,
) -> tuple[float, str]:
    """Average per-work-style affinity scores into one (score, reason).

    Multiple work styles average their per-bundle weights — explicit
    and predictable per the plan's guidance.
    """
    if not work_styles:
        return 0.0, f"No work styles → no affinity for {bundle.id}"
    per_style: list[float] = [_WORKSTYLE_BUNDLE_AFFINITY.get(ws, {}).get(bundle.id, 0.0) for ws in work_styles]
    avg = sum(per_style) / len(per_style)
    # Clamp to the contracted range.
    avg = max(-1.0, min(1.0, avg))
    if avg > 0:
        styles_label = "/".join(work_styles)
        return avg, f"Work style {styles_label} → {bundle.id} fits"
    return avg, f"Work styles do not particularly favour {bundle.id}"


def _cloud_match(cloud: CloudPosture, bundle: VanerPolicyBundle) -> tuple[float, str]:
    """Reward bundles that match the user's cloud posture preference."""
    posture = bundle.local_cloud_posture
    if cloud == "local_only":
        if posture == "local_only":
            return 1.0, f"Local-only requested → {bundle.id} is local_only"
        if posture == "local_preferred":
            return 0.3, f"Local-only requested → {bundle.id} is local-preferred"
        return -1.0, f"Local-only requested → {bundle.id} reaches for cloud"
    if cloud == "ask_first":
        if posture == "local_preferred":
            return 1.0, f"Ask-first → {bundle.id} prefers local but can hybrid"
        if posture in ("local_only", "hybrid"):
            return 0.4, f"Ask-first → {bundle.id} keeps cloud opt-in"
        return -0.5, f"Ask-first → {bundle.id} leans cloud"
    if cloud == "hybrid_when_worth_it":
        if posture == "hybrid":
            return 1.0, f"Hybrid-when-worth-it → {bundle.id} is hybrid"
        if posture == "local_preferred":
            return 0.4, f"Hybrid-when-worth-it → {bundle.id} prefers local"
        if posture == "cloud_preferred":
            return 0.2, f"Hybrid-when-worth-it → {bundle.id} prefers cloud"
        return -0.2, f"Hybrid-when-worth-it → {bundle.id} is local_only"
    # cloud == "best_available"
    if posture == "cloud_preferred":
        return 1.0, f"Best-available → {bundle.id} prefers cloud"
    if posture == "hybrid":
        return 0.6, f"Best-available → {bundle.id} is hybrid"
    return -0.4, f"Best-available → {bundle.id} stays local"


def _resource_match(
    compute: ComputePosture,
    bundle: VanerPolicyBundle,
) -> tuple[float, str]:
    """Map compute posture to runtime / horizon weight."""
    if compute == "light":
        if bundle.runtime_profile == "small":
            return 1.0, f"Light compute posture → {bundle.id} runs small"
        if bundle.runtime_profile == "medium":
            return 0.0, f"Light compute posture → {bundle.id} runs medium"
        return -0.6, f"Light compute posture → {bundle.id} runs large"
    if compute == "available_power":
        if bundle.runtime_profile == "large":
            return 1.0, f"Available-power posture → {bundle.id} runs large"
        if bundle.runtime_profile == "medium":
            return 0.3, f"Available-power posture → {bundle.id} runs medium"
        return -0.2, f"Available-power posture → {bundle.id} runs small"
    # compute == "balanced"
    if bundle.runtime_profile == "medium":
        return 0.6, f"Balanced compute posture → {bundle.id} runs medium"
    return 0.1, f"Balanced compute posture → {bundle.id} is acceptable"


def _background_match(
    background: BackgroundPosture,
    bundle: VanerPolicyBundle,
) -> tuple[float, str]:
    """Map background-pondering posture to deep-run profile."""
    deep = bundle.deep_run_profile
    if background == "minimal":
        if deep == "conservative":
            return 1.0, f"Minimal background → {bundle.id} is conservative"
        if deep == "balanced":
            return 0.0, f"Minimal background → {bundle.id} is balanced"
        return -0.6, f"Minimal background → {bundle.id} is aggressive"
    if background == "normal":
        if deep == "balanced":
            return 1.0, f"Normal background → {bundle.id} is balanced"
        return 0.2, f"Normal background → {bundle.id} is {deep}"
    if background == "idle_more":
        if deep == "balanced":
            return 0.6, f"Idle-more background → {bundle.id} is balanced"
        if deep == "aggressive":
            return 0.8, f"Idle-more background → {bundle.id} is aggressive"
        return -0.2, f"Idle-more background → {bundle.id} is conservative"
    # background == "deep_run_aggressive"
    if bundle.id in ("deep_research", "hybrid_quality"):
        return 1.0, f"Deep-run-aggressive → {bundle.id} matches"
    if deep == "aggressive":
        return 0.7, f"Deep-run-aggressive → {bundle.id} is aggressive"
    if deep == "balanced":
        return 0.0, f"Deep-run-aggressive → {bundle.id} is balanced"
    return -0.5, f"Deep-run-aggressive → {bundle.id} is conservative"


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def _score_bundle(
    answers: SetupAnswers,
    hardware: HardwareProfile,
    bundle: VanerPolicyBundle,
) -> tuple[float, tuple[str, ...]]:
    """Run every match function and return the summed score + reasons.

    Only reasons whose component score is strictly positive are
    included — the disclosure panel should explain why this bundle
    *won*, not the long list of negatives. Reason order is fixed so
    the result is byte-deterministic.
    """
    components: list[tuple[float, str]] = [
        _priority_match(answers.priority, bundle),
        _hardware_match(hardware.tier, bundle),
        _workstyle_match(answers.work_styles, bundle),
        _cloud_match(answers.cloud_posture, bundle),
        _resource_match(answers.compute_posture, bundle),
        _background_match(answers.background_posture, bundle),
    ]
    total = sum(score for score, _ in components)
    positive_reasons = tuple(reason for score, reason in components if score > 0.0)
    return total, positive_reasons


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


_FORCED_FALLBACK_BUNDLE_ID: str = "local_lightweight"


def select_policy_bundle(
    answers: SetupAnswers,
    hardware: HardwareProfile,
) -> SelectionResult:
    """Pick the best-fit policy bundle for the given answers + hardware.

    Pure function — same inputs always produce the exact same
    :class:`SelectionResult` (including ``reasons`` and ``runner_ups``
    ordering). Implementation is filter → score → pick with a
    deterministic alphabetical-id tiebreaker.
    """

    kept, _dropped = _apply_filters(answers, hardware, PROFILE_CATALOG)

    forced_fallback = False
    if not kept:
        # Relax the priority + hardware filters; cloud posture is the
        # one explicit user wish we do not silently widen.
        relaxed_filters: tuple[_FilterFn, ...] = (_filter_cloud_posture,)
        kept, _dropped = _apply_filters(
            answers,
            hardware,
            PROFILE_CATALOG,
            filters=relaxed_filters,
        )

    if not kept:
        # Even cloud-posture-only filtering wiped the catalogue
        # (theoretical — cloud_posture filters never drop more than
        # five bundles). Hand back the safe local default and flag.
        forced_fallback = True
        fallback = bundle_by_id(_FORCED_FALLBACK_BUNDLE_ID)
        return SelectionResult(
            bundle=fallback,
            score=0.0,
            reasons=("No bundle matched filters; returning safest local default.",),
            runner_ups=(),
            forced_fallback=True,
        )

    scored: list[tuple[float, str, VanerPolicyBundle, tuple[str, ...]]] = []
    for bundle in kept:
        score, reasons = _score_bundle(answers, hardware, bundle)
        scored.append((score, bundle.id, bundle, reasons))

    # Deterministic ordering: highest score first; alphabetical id
    # breaks ties. Sorting on a (negated-score, id) key gives a
    # stable, total order — Python's sort is also stable so even
    # equal keys preserve catalogue order.
    scored.sort(key=lambda item: (-item[0], item[1]))

    chosen_score, _chosen_id, chosen_bundle, chosen_reasons = scored[0]
    runner_ups = tuple(item[2] for item in scored[1:3])

    logger.debug(
        "select_policy_bundle picked %s with score %.3f (forced_fallback=%s)",
        chosen_bundle.id,
        chosen_score,
        forced_fallback,
    )
    return SelectionResult(
        bundle=chosen_bundle,
        score=chosen_score,
        reasons=chosen_reasons,
        runner_ups=runner_ups,
        forced_fallback=forced_fallback,
    )


__all__ = [
    "SelectionResult",
    "select_policy_bundle",
]
