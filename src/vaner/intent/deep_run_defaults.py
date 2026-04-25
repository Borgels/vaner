# SPDX-License-Identifier: Apache-2.0
"""WS9 — Deep-Run defaults derived from the active policy bundle (0.8.6).

Pure function ``deep_run_defaults_for(bundle, setup)`` translates a
:class:`VanerPolicyBundle` plus the user's Simple-Mode answers
(:class:`SetupAnswers`) into a sensible seed for the Deep-Run session
start dialog. The user can still override every field at session-start
time — this just removes the "blank form" experience.

Single source of truth for CLI / desktop / cockpit pre-fills: every
surface that asks the user to start a Deep-Run reads from this
function (directly when the daemon is co-located, or via
``GET /deep-run/defaults`` / ``vaner.deep_run.defaults`` MCP).

The mapping is deliberately small and lookup-shaped — adding a new
bundle or background posture is a one-line change. Existing 0.8.3
Deep-Run primitives are *not* touched; this module only computes a
seed that callers thread through to ``DeepRunSession.new(...)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from vaner.intent.deep_run import (
    DeepRunFocus,
    DeepRunHorizonBias,
    DeepRunLocality,
    DeepRunPreset,
)
from vaner.setup.answers import SetupAnswers
from vaner.setup.policy import VanerPolicyBundle

__all__ = [
    "DeepRunDefaults",
    "deep_run_defaults_for",
    "defaults_to_dict",
]


@dataclass(frozen=True, slots=True)
class DeepRunDefaults:
    """Seed values for the Deep-Run start dialog.

    All fields are the existing 0.8.3 storage literals — only the
    *value selection* is bundle-derived. The session schema does not
    change.

    ``reasons`` is a short list of human-readable explanations, one per
    derived field, suitable for showing under the pre-filled values in
    a desktop dialog or the CLI confirmation panel.
    """

    preset: DeepRunPreset
    horizon_bias: DeepRunHorizonBias
    locality: DeepRunLocality
    cost_cap_usd: float
    focus: DeepRunFocus
    source_bundle_id: str
    reasons: tuple[str, ...]


# Mapping tables. Kept tiny and at module scope so adding a new bundle
# posture / spend tier / background mode is a one-line edit.

_LOCALITY_BY_POSTURE: dict[str, DeepRunLocality] = {
    "local_only": "local_only",
    "local_preferred": "local_preferred",
    "hybrid": "local_preferred",  # conservative: still default to local-pref for an away window
    "cloud_preferred": "allow_cloud",
}

_COST_CAP_BY_SPEND: dict[str, float] = {
    "zero": 0.0,
    "low": 1.0,
    "medium": 2.0,
    "high": 5.0,
}

_FOCUS_BY_BACKGROUND: dict[str, DeepRunFocus] = {
    "deep_run_aggressive": "all_recent",
    "idle_more": "active_goals",
    "normal": "active_goals",
    "minimal": "current_workspace",
}


def deep_run_defaults_for(
    bundle: VanerPolicyBundle,
    setup: SetupAnswers,
) -> DeepRunDefaults:
    """Translate a bundle + Simple-Mode answers into Deep-Run seeds.

    Pure function. Same inputs always yield the same output (used by
    the determinism test). Tie-breaks on ``prediction_horizon_bias``
    weights are alphabetical on the key, so the function is fully
    deterministic across Python hash randomisation.
    """

    # Preset comes straight off the bundle.
    preset: DeepRunPreset = bundle.deep_run_profile

    # Horizon bias: pick the highest-weighted key from the bundle's
    # mapping; ties broken by alphabetical order on the key.
    weighted = sorted(
        bundle.prediction_horizon_bias.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    horizon_bias: DeepRunHorizonBias = weighted[0][0]

    locality: DeepRunLocality = _LOCALITY_BY_POSTURE.get(bundle.local_cloud_posture, "local_preferred")
    cost_cap_usd: float = _COST_CAP_BY_SPEND.get(bundle.spend_profile, 0.0)
    focus: DeepRunFocus = _FOCUS_BY_BACKGROUND.get(setup.background_posture, "active_goals")

    reasons = (
        f"Bundle {bundle.id} → preset {preset}",
        f"Bundle prediction_horizon_bias max → horizon_bias {horizon_bias}",
        f"Bundle local_cloud_posture {bundle.local_cloud_posture} → locality {locality}",
        f"Bundle spend_profile {bundle.spend_profile} → cost_cap_usd {cost_cap_usd:.2f}",
        f"Setup background_posture {setup.background_posture} → focus {focus}",
    )

    return DeepRunDefaults(
        preset=preset,
        horizon_bias=horizon_bias,
        locality=locality,
        cost_cap_usd=cost_cap_usd,
        focus=focus,
        source_bundle_id=bundle.id,
        reasons=reasons,
    )


def defaults_to_dict(defaults: DeepRunDefaults) -> dict[str, object]:
    """Serialise a :class:`DeepRunDefaults` to a JSON-safe dict.

    Shared by the HTTP endpoint and the MCP tool so both surfaces emit
    the same payload shape. Field order matches the dataclass.
    """

    return {
        "preset": defaults.preset,
        "horizon_bias": defaults.horizon_bias,
        "locality": defaults.locality,
        "cost_cap_usd": defaults.cost_cap_usd,
        "focus": defaults.focus,
        "source_bundle_id": defaults.source_bundle_id,
        "reasons": list(defaults.reasons),
    }
