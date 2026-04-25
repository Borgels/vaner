# SPDX-License-Identifier: Apache-2.0
"""WS1 — Setup primitives: PROFILE_CATALOG (0.8.6).

The seven shipped policy bundles per spec §9. This is the single source
of truth — desktop apps, the CLI wizard, the cockpit summary card, and
the MCP/HTTP setup tools all read from this tuple.

Numeric values for the per-bundle multipliers (drafting_aggressiveness,
exploration_ratio, persistence_strength, goal_weighting,
prediction_horizon_bias) are calibrated against the Deep-Run preset
table (see :mod:`vaner.intent.deep_run_policy`) so that bundle defaults
compose multiplicatively with any Deep-Run session deltas without
double-application.

Bundle scale:

- ``drafting_aggressiveness``: ``0.7`` (cautious) → ``1.3`` (eager).
  Multiplier on the drafter evidence threshold.
- ``exploration_ratio``: ``-0.10`` (exploit-leaning) → ``+0.15``
  (explore-leaning). Additive bias on top of base exploration weights.
- ``persistence_strength``: ``0.6`` (decay fast) → ``1.5`` (decay
  slow). Multiplier on memory retention.
- ``goal_weighting``: ``0.8`` → ``1.4``. Multiplier on goal-aligned
  prediction scoring.
- ``prediction_horizon_bias``: weights sum *roughly* to 4.0 across the
  four buckets; the engine normalises before applying.
"""

from __future__ import annotations

from typing import Final

from vaner.setup.policy import VanerPolicyBundle

# ---------------------------------------------------------------------------
# Bundle definitions. Order matches spec §9 narrative; ids are the
# stable foreign key persisted to ``PolicyConfig.selected_bundle_id``.
# ---------------------------------------------------------------------------


_LOCAL_LIGHTWEIGHT = VanerPolicyBundle(
    # Target user: laptop on battery, integrated GPU, wants Vaner to
    # not heat the room. Local-only by default; minimal background.
    id="local_lightweight",
    label="Local Lightweight",
    description=("Run entirely on this device with a small local model. Best for older laptops, battery, and privacy-first users."),
    local_cloud_posture="local_only",
    runtime_profile="small",
    spend_profile="zero",
    latency_profile="snappy",
    privacy_profile="strict",
    prediction_horizon_bias={
        "likely_next": 1.4,
        "long_horizon": 0.8,
        "finish_partials": 1.2,
        "balanced": 0.6,
    },
    drafting_aggressiveness=0.8,
    exploration_ratio=-0.05,
    persistence_strength=0.7,
    goal_weighting=1.0,
    context_injection_default="digest_only",
    deep_run_profile="conservative",
)


_LOCAL_BALANCED = VanerPolicyBundle(
    # Target user: capable laptop or desktop, mid-tier local model
    # (e.g. 7-13B). Local-first but willing to ponder more in the
    # background. Default for the "Capable" hardware tier.
    id="local_balanced",
    label="Local Balanced",
    description=("Local-first with a mid-sized model. Solid default for a capable laptop or desktop with no cloud reliance."),
    local_cloud_posture="local_preferred",
    runtime_profile="medium",
    spend_profile="zero",
    latency_profile="balanced",
    privacy_profile="strict",
    prediction_horizon_bias={
        "likely_next": 1.0,
        "long_horizon": 1.0,
        "finish_partials": 1.0,
        "balanced": 1.0,
    },
    drafting_aggressiveness=1.0,
    exploration_ratio=0.0,
    persistence_strength=1.0,
    goal_weighting=1.0,
    context_injection_default="policy_hybrid",
    deep_run_profile="balanced",
)


_LOCAL_HEAVY = VanerPolicyBundle(
    # Target user: workstation or homelab with a large local model
    # (e.g. 30-70B at Q4+). Local-only, but Vaner is allowed to ponder
    # aggressively because the box can take it.
    id="local_heavy",
    label="Local Heavy",
    description=("All-local with a large model and aggressive background pondering. Built for workstations and homelab GPUs."),
    local_cloud_posture="local_only",
    runtime_profile="large",
    spend_profile="zero",
    latency_profile="quality",
    privacy_profile="strict",
    prediction_horizon_bias={
        "likely_next": 1.0,
        "long_horizon": 1.4,
        "finish_partials": 1.0,
        "balanced": 1.2,
    },
    drafting_aggressiveness=1.1,
    exploration_ratio=0.10,
    persistence_strength=1.3,
    goal_weighting=1.2,
    context_injection_default="policy_hybrid",
    deep_run_profile="aggressive",
)


_HYBRID_BALANCED = VanerPolicyBundle(
    # Target user: anyone who's fine with the engine reaching for a
    # cloud model when local is genuinely insufficient. The default
    # bundle for first-run when hardware tier is unknown — safe
    # middle path.
    id="hybrid_balanced",
    label="Hybrid Balanced",
    description=("Local for everyday work, cloud only when it's clearly worth it. Recommended default for most users."),
    local_cloud_posture="hybrid",
    runtime_profile="medium",
    spend_profile="low",
    latency_profile="balanced",
    privacy_profile="standard",
    prediction_horizon_bias={
        "likely_next": 1.0,
        "long_horizon": 1.1,
        "finish_partials": 1.0,
        "balanced": 1.0,
    },
    drafting_aggressiveness=1.0,
    exploration_ratio=0.05,
    persistence_strength=1.0,
    goal_weighting=1.0,
    context_injection_default="policy_hybrid",
    deep_run_profile="balanced",
)


_HYBRID_QUALITY = VanerPolicyBundle(
    # Target user: serious work where the user is willing to spend on
    # cloud capacity to get the best result. Still hybrid, but the
    # cloud bias is set higher and the latency profile prioritises
    # quality over snappiness.
    id="hybrid_quality",
    label="Hybrid Quality",
    description=("Reach for the best available model — local or cloud — and trade some snappiness and budget for higher answer quality."),
    local_cloud_posture="cloud_preferred",
    runtime_profile="large",
    spend_profile="medium",
    latency_profile="quality",
    privacy_profile="standard",
    prediction_horizon_bias={
        "likely_next": 0.9,
        "long_horizon": 1.4,
        "finish_partials": 1.0,
        "balanced": 1.1,
    },
    drafting_aggressiveness=1.2,
    exploration_ratio=0.10,
    persistence_strength=1.2,
    goal_weighting=1.2,
    context_injection_default="top_match_auto_include",
    deep_run_profile="balanced",
)


_COST_SAVER = VanerPolicyBundle(
    # Target user: cost-conscious user who still wants some hybrid
    # capacity. Tightens spend cap and prefers the cheapest endpoint
    # that meets the latency / context floor.
    id="cost_saver",
    label="Cost Saver",
    description=("Hybrid setup with tight cloud spend caps. Prefers the cheapest endpoint that still meets the bar."),
    local_cloud_posture="local_preferred",
    runtime_profile="small",
    spend_profile="low",
    latency_profile="balanced",
    privacy_profile="standard",
    prediction_horizon_bias={
        "likely_next": 1.2,
        "long_horizon": 0.9,
        "finish_partials": 1.1,
        "balanced": 0.8,
    },
    drafting_aggressiveness=0.9,
    exploration_ratio=-0.05,
    persistence_strength=0.9,
    goal_weighting=1.0,
    context_injection_default="adopted_package_only",
    deep_run_profile="conservative",
)


_DEEP_RESEARCH = VanerPolicyBundle(
    # Target user: long-horizon research / writing work where Vaner
    # should ponder broadly and persistently. Overnight Deep-Run is the
    # natural mode; daytime use composes the bundle's bias with the
    # active session's preset.
    id="deep_research",
    label="Deep Research",
    description=("Long-horizon, broad exploration for research and writing. Persists predictions longer and prefers depth over speed."),
    local_cloud_posture="hybrid",
    runtime_profile="large",
    spend_profile="medium",
    latency_profile="quality",
    privacy_profile="standard",
    prediction_horizon_bias={
        "likely_next": 0.7,
        "long_horizon": 1.6,
        "finish_partials": 1.1,
        "balanced": 1.2,
    },
    drafting_aggressiveness=1.1,
    exploration_ratio=0.15,
    persistence_strength=1.5,
    goal_weighting=1.4,
    context_injection_default="policy_hybrid",
    deep_run_profile="aggressive",
)


PROFILE_CATALOG: Final[tuple[VanerPolicyBundle, ...]] = (
    _LOCAL_LIGHTWEIGHT,
    _LOCAL_BALANCED,
    _LOCAL_HEAVY,
    _HYBRID_BALANCED,
    _HYBRID_QUALITY,
    _COST_SAVER,
    _DEEP_RESEARCH,
)


# Internal id-keyed lookup map. Built once at import time so
# ``bundle_by_id`` is O(1). Not exported — callers should use the
# helper, which gives a clearer error message on miss.
_BY_ID: Final[dict[str, VanerPolicyBundle]] = {bundle.id: bundle for bundle in PROFILE_CATALOG}


def bundle_by_id(bundle_id: str) -> VanerPolicyBundle:
    """Return the bundle with the given id.

    Raises :class:`KeyError` (with a helpful message) if no bundle in
    :data:`PROFILE_CATALOG` matches. The engine should never silently
    fall back to a default for an unknown id — that would mask a
    config-shape bug or a corrupted ``.vaner/config.toml``.
    """

    try:
        return _BY_ID[bundle_id]
    except KeyError as exc:
        known = ", ".join(sorted(_BY_ID))
        raise KeyError(f"unknown bundle id {bundle_id!r}; known ids: {known}") from exc


__all__ = [
    "PROFILE_CATALOG",
    "bundle_by_id",
]
