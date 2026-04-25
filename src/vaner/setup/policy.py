# SPDX-License-Identifier: Apache-2.0
"""WS1 — Setup primitives: VanerPolicyBundle dataclass (0.8.6).

A *policy bundle* is the intermediate layer between Simple-Mode wizard
answers and the engine's existing knobs. Each bundle:

- Names an outcome-level archetype (e.g. *"Local Balanced"*, *"Hybrid
  Quality"*) that a non-technical user can recognise.
- Carries the seven dimensions the engine actually consults
  (cloud posture, runtime profile, spend profile, latency profile,
  privacy profile, prediction-horizon bias, drafting aggressiveness,
  exploration ratio, persistence strength, goal weighting,
  context-injection default, deep-run profile).
- Composes additively with Deep-Run preset overrides at session start
  (see :mod:`vaner.intent.deep_run_policy`); bundles set the *baseline*,
  Deep-Run sessions layer additive overrides on top.

The single source of truth for the seven shipped bundles is
:data:`vaner.setup.catalog.PROFILE_CATALOG`. WS3's selection algorithm
filters and scores that catalogue against the user's
:class:`SetupAnswers` and :class:`HardwareProfile`.

The bundle dataclass is **frozen**: bundles are pure data, never mutated.
``PolicyConfig.bundle_overrides`` (a free-form dict on the config
schema) carries the user's per-knob deviations from the selected
bundle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from vaner.intent.deep_run import DeepRunPreset

# Mirror of ``ContextInjectionConfig.mode`` (see
# :mod:`vaner.models.config`). Repeated here verbatim because the source
# Literal is declared inline on the field rather than as a named alias.
# Keep these two lists in sync; a mismatch surfaces as a Pydantic
# validation error when the bundle's value flows into the integration
# config.
ContextInjectionMode = Literal[
    "none",
    "digest_only",
    "adopted_package_only",
    "top_match_auto_include",
    "policy_hybrid",
    "client_controlled",
]

# The four horizon-bias buckets surfaced to the engine's frontier
# scoring. These are the same four values used by Deep-Run's
# ``HorizonBiasSpec`` (see :mod:`vaner.intent.deep_run_policy`); the
# bundle's ``prediction_horizon_bias`` mapping is a per-bundle weight
# distribution over these buckets, *not* a single chosen mode. The
# engine multiplies these weights into its frontier scoring at cycle
# time.
PredictionHorizonKey = Literal[
    "likely_next",
    "long_horizon",
    "finish_partials",
    "balanced",
]


@dataclass(frozen=True, slots=True)
class VanerPolicyBundle:
    """One outcome-level policy bundle.

    Field meanings (spec §8):

    - ``id`` — stable kebab-case identifier; used as the foreign key in
      ``PolicyConfig.selected_bundle_id``. Never displayed to users.
    - ``label`` — short human-readable name (Title Case). Shown in the
      desktop summary card and the CLI ``vaner setup show`` output.
    - ``description`` — one-sentence explanation of who the bundle is
      for. Shown under the label in any surface that has the room.
    - ``local_cloud_posture`` — flows into ``BackendConfig.prefer_local``,
      the ``ExplorationConfig.endpoints`` cloud filter, and the
      Deep-Run default ``locality``.
    - ``runtime_profile`` — selects the local-runtime preference band
      (``"small"`` / ``"medium"`` / ``"large"``) the exploration pool
      consults when more than one local model is detected.
    - ``spend_profile`` — drives ``BackendConfig.remote_budget_per_hour``
      and the Deep-Run default ``cost_cap_usd``.
    - ``latency_profile`` — informs the exploration pool's
      latency-vs-quality tie-break.
    - ``privacy_profile`` — interacts with cloud_posture; sets the
      strictness floor that the user cannot accidentally relax via a
      bundle re-selection (see WS5 invariant test).
    - ``prediction_horizon_bias`` — frozen mapping over the four
      horizon-bias keys; weights compose multiplicatively with
      Deep-Run's ``HorizonBiasSpec`` at session time.
    - ``drafting_aggressiveness`` — multiplier on the drafter's
      evidence threshold. ``1.0`` is neutral; ``< 1.0`` requires more
      evidence (conservative drafts), ``> 1.0`` is more permissive.
    - ``exploration_ratio`` — added to ``ExplorationConfig`` invest /
      exploit balance.
    - ``persistence_strength`` — multiplier on memory-decay resistance;
      higher values keep predictions warm longer.
    - ``goal_weighting`` — multiplier on goal-aligned prediction
      scoring (see ``IntentConfig`` goal-weighting in 0.8.2+).
    - ``context_injection_default`` — per-tier default for
      ``IntegrationsConfig.context_injection.mode``. Capability tiers
      may downgrade this floor at run time.
    - ``deep_run_profile`` — default Deep-Run preset when the user
      opens "Start Deep-Run".
    """

    id: str
    label: str
    description: str
    local_cloud_posture: Literal[
        "local_only",
        "local_preferred",
        "hybrid",
        "cloud_preferred",
    ]
    runtime_profile: Literal["small", "medium", "large", "auto"]
    spend_profile: Literal["zero", "low", "medium", "high"]
    latency_profile: Literal["snappy", "balanced", "quality"]
    privacy_profile: Literal["strict", "standard", "relaxed"]
    prediction_horizon_bias: Mapping[PredictionHorizonKey, float]
    drafting_aggressiveness: float
    exploration_ratio: float
    persistence_strength: float
    goal_weighting: float
    context_injection_default: ContextInjectionMode
    deep_run_profile: DeepRunPreset

    # Internal: bundles are intended to be defined as module-level
    # constants. The ``__post_init__`` freeze step ensures any
    # ``prediction_horizon_bias`` passed as a plain ``dict`` is wrapped
    # in a ``MappingProxyType`` so the bundle remains structurally
    # immutable (the dataclass itself is already frozen against
    # attribute reassignment).
    def __post_init__(self) -> None:
        # Use object.__setattr__ because the dataclass is frozen.
        if not isinstance(self.prediction_horizon_bias, MappingProxyType):
            object.__setattr__(
                self,
                "prediction_horizon_bias",
                MappingProxyType(dict(self.prediction_horizon_bias)),
            )
        # Validate the mapping keys match exactly the four expected
        # literals — guards against typos in catalogue entries.
        expected_keys = {"likely_next", "long_horizon", "finish_partials", "balanced"}
        actual_keys = set(self.prediction_horizon_bias.keys())
        if actual_keys != expected_keys:
            missing = expected_keys - actual_keys
            extra = actual_keys - expected_keys
            raise ValueError(
                f"VanerPolicyBundle.prediction_horizon_bias keys must be exactly "
                f"{sorted(expected_keys)}; missing={sorted(missing)} extra={sorted(extra)}"
            )


# Re-export for convenience; downstream modules can import everything
# they need from ``vaner.setup.policy``.
__all__ = [
    "ContextInjectionMode",
    "PredictionHorizonKey",
    "VanerPolicyBundle",
]
