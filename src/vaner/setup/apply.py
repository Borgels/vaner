# SPDX-License-Identifier: Apache-2.0
"""WS5 — Apply policy bundle as governor input (0.8.6).

This module is the bridge between the outcome-level policy bundles
(see :mod:`vaner.setup.policy` / :mod:`vaner.setup.catalog`) and the
engine's existing knob-level config (see :mod:`vaner.models.config`).

The single entry point is :func:`apply_policy_bundle`. It is a **pure
function** — it never mutates the input ``VanerConfig``; it returns a
new :class:`AppliedPolicy` that wraps the materialised config plus a
human-readable list of overrides.

Order of precedence (per plan WS5):

1. Bundle defaults overwrite specific config-derived fields.
2. ``user_overrides`` (read by the caller from
   :attr:`PolicyConfig.bundle_overrides`) layer on top.
3. Deep-Run session deltas are applied at session start by the
   Deep-Run engine — *not* here. ``apply_policy_bundle`` handles the
   background-mode defaults only.

Cloud-widening guard
--------------------

If the new bundle's :pyattr:`VanerPolicyBundle.local_cloud_posture` is
strictly more permissive than the previous bundle's posture (e.g.
``local_only`` → ``hybrid``), the function inserts a sentinel string
into :pyattr:`AppliedPolicy.overrides_applied`. Callers (CLI / desktop
UI) are expected to surface this to the user before persisting the
change. The function itself does not block — that is a UX policy, not
a domain invariant.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from vaner.models.config import VanerConfig
from vaner.setup.catalog import bundle_by_id
from vaner.setup.policy import VanerPolicyBundle

__all__ = [
    "AppliedPolicy",
    "apply_policy_bundle",
]


# ---------------------------------------------------------------------------
# Posture / spend / runtime mapping tables
# ---------------------------------------------------------------------------


# The four ``local_cloud_posture`` literals ranked by how much cloud
# they admit. Used only to decide whether one posture is *strictly more
# permissive* than another for the cloud-widening guard.
_POSTURE_RANK: Final[Mapping[str, int]] = {
    "local_only": 0,
    "local_preferred": 1,
    "hybrid": 2,
    "cloud_preferred": 3,
}


# Spend profile -> ``BackendConfig.remote_budget_per_hour``. Numbers are
# the bundle-default budget envelope; user overrides can still change
# them. ``zero`` means *no remote spend permitted by default*.
_SPEND_TO_REMOTE_BUDGET_PER_HOUR: Final[Mapping[str, int]] = {
    "zero": 0,
    "low": 30,
    "medium": 60,
    "high": 120,
}


# Sentinel prefix for the cloud-widening flag in
# ``AppliedPolicy.overrides_applied``. Callers can detect widening with
# a simple ``startswith`` check rather than parsing the diff.
WIDENS_CLOUD_POSTURE_SENTINEL: Final[str] = "WIDENS_CLOUD_POSTURE"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppliedPolicy:
    """The result of applying a bundle to a config.

    Carries the new :class:`VanerConfig` plus a human-readable list of
    overrides that were applied — useful for the transparency panel.
    The bundle id is preserved separately so callers can diff against
    the user's selection.

    Attributes:
        config: New :class:`VanerConfig` with bundle defaults +
            user overrides applied. Original input config is never
            mutated.
        bundle_id: The id of the bundle that was applied. Matches
            :attr:`PolicyConfig.selected_bundle_id` after the caller
            persists the result.
        overrides_applied: Human-readable lines describing each
            override that was written. Includes informational lines
            for fields the function intentionally does *not* write
            (e.g. Deep-Run defaults, scoring multipliers without a
            matching engine knob). May contain a
            ``WIDENS_CLOUD_POSTURE`` sentinel string when the new
            bundle's cloud posture is strictly more permissive than
            the previous bundle's.
    """

    config: VanerConfig
    bundle_id: str
    overrides_applied: tuple[str, ...]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_policy_bundle(
    config: VanerConfig,
    bundle: VanerPolicyBundle,
    *,
    user_overrides: Mapping[str, Any] | None = None,
) -> AppliedPolicy:
    """Materialise bundle defaults onto a :class:`VanerConfig`.

    Pure function. The input ``config`` is never mutated; this returns
    a new :class:`VanerConfig` (via Pydantic's ``model_copy``) and an
    audit list describing every override that was applied.

    Apply order:

    1. Bundle defaults overwrite specific config-derived fields
       (:class:`BackendConfig`, :class:`ExplorationConfig`,
       :class:`IntegrationsConfig.context_injection`).
    2. ``user_overrides`` layer on top — concretely, today the only
       supported user-override key is ``"context_injection_mode"``.
       (When :attr:`PolicyConfig.bundle_overrides` grows additional
       knobs, extend this function in lock-step.)
    3. Deep-Run session deltas are applied by the Deep-Run engine at
       session start; this function does not pre-write Deep-Run
       fields. The bundle's Deep-Run defaults are recorded in the
       audit list so the transparency panel can show them, but they
       are never written into ``DeepRunSession`` here.

    Cloud-widening guard:

    If ``bundle.local_cloud_posture`` is strictly more permissive
    than the previous bundle's posture (looked up from
    ``config.policy.selected_bundle_id``), the audit list contains a
    sentinel string starting with
    :data:`WIDENS_CLOUD_POSTURE_SENTINEL`. Callers should surface
    this to the user before persisting the change.

    Args:
        config: The current :class:`VanerConfig`. Treated as
            immutable.
        bundle: The :class:`VanerPolicyBundle` to apply.
        user_overrides: Optional mapping of per-knob user overrides
            on top of the bundle defaults. Pulled by the caller from
            ``config.policy.bundle_overrides``.

    Returns:
        :class:`AppliedPolicy` carrying the materialised config and
        an audit list of every override applied.
    """

    overrides_user: Mapping[str, Any] = user_overrides if user_overrides is not None else {}
    overrides_applied: list[str] = []

    # --------------------------------------------------------------
    # 0. Cloud-widening detection (against the *previous* bundle).
    # --------------------------------------------------------------
    prior_bundle_id = config.policy.selected_bundle_id
    if prior_bundle_id and prior_bundle_id != bundle.id:
        try:
            prior_bundle = bundle_by_id(prior_bundle_id)
        except KeyError:
            prior_bundle = None
        if prior_bundle is not None:
            prior_rank = _POSTURE_RANK.get(prior_bundle.local_cloud_posture, 0)
            new_rank = _POSTURE_RANK.get(bundle.local_cloud_posture, 0)
            if new_rank > prior_rank:
                overrides_applied.append(
                    f"{WIDENS_CLOUD_POSTURE_SENTINEL}: {prior_bundle.local_cloud_posture}->{bundle.local_cloud_posture}"
                )

    # --------------------------------------------------------------
    # 1. BackendConfig — local/cloud posture and remote spend cap.
    # --------------------------------------------------------------
    new_prefer_local = bundle.local_cloud_posture in ("local_only", "local_preferred")
    new_remote_budget = _SPEND_TO_REMOTE_BUDGET_PER_HOUR.get(
        bundle.spend_profile,
        config.backend.remote_budget_per_hour,
    )
    backend_updates: dict[str, Any] = {}
    if config.backend.prefer_local != new_prefer_local:
        backend_updates["prefer_local"] = new_prefer_local
        overrides_applied.append(f"BackendConfig.prefer_local: {new_prefer_local}")
    if config.backend.remote_budget_per_hour != new_remote_budget:
        backend_updates["remote_budget_per_hour"] = new_remote_budget
        overrides_applied.append(f"BackendConfig.remote_budget_per_hour: {new_remote_budget}")
    new_backend = config.backend.model_copy(update=backend_updates) if backend_updates else config.backend

    # --------------------------------------------------------------
    # 2. ExplorationConfig — endpoint filter + economics-first routing.
    # --------------------------------------------------------------
    exploration_updates: dict[str, Any] = {}
    if bundle.local_cloud_posture == "local_only" and config.exploration.endpoints:
        kept = tuple(ep for ep in config.exploration.endpoints if _is_localhost_url(ep.url))
        if len(kept) != len(config.exploration.endpoints):
            exploration_updates["endpoints"] = list(kept)
            overrides_applied.append(
                f"ExplorationConfig.endpoints: filtered to localhost only ({len(kept)} of {len(config.exploration.endpoints)} kept)"
            )

    # economics_first_routing — only flip on when the bundle is
    # cost-sensitive (small/large runtime profile + low/zero spend).
    # We never silently flip it OFF; that is a user knob.
    cost_sensitive = bundle.runtime_profile in ("small", "large") and bundle.spend_profile in ("zero", "low")
    if cost_sensitive and not config.exploration.economics_first_routing:
        exploration_updates["economics_first_routing"] = True
        overrides_applied.append("ExplorationConfig.economics_first_routing: True")

    new_exploration = config.exploration.model_copy(update=exploration_updates) if exploration_updates else config.exploration

    # --------------------------------------------------------------
    # 3. IntegrationsConfig.context_injection.mode — bundle default
    #    unless the user has set their own override.
    # --------------------------------------------------------------
    user_ci_override = overrides_user.get("context_injection_mode")
    new_integrations = config.integrations
    if user_ci_override is not None:
        # User wins. Apply their override to the integrations config
        # if it differs from the current value.
        if user_ci_override != config.integrations.context_injection.mode:
            new_ci = config.integrations.context_injection.model_copy(update={"mode": user_ci_override})
            new_integrations = config.integrations.model_copy(update={"context_injection": new_ci})
            overrides_applied.append(f"IntegrationsConfig.context_injection.mode: {user_ci_override} (user override)")
        else:
            overrides_applied.append(f"IntegrationsConfig.context_injection.mode: {user_ci_override} (user override, no-op)")
    else:
        # No user override; bundle default applies.
        if bundle.context_injection_default != config.integrations.context_injection.mode:
            new_ci = config.integrations.context_injection.model_copy(update={"mode": bundle.context_injection_default})
            new_integrations = config.integrations.model_copy(update={"context_injection": new_ci})
            overrides_applied.append(f"IntegrationsConfig.context_injection.mode: {bundle.context_injection_default}")

    # --------------------------------------------------------------
    # 4. Informational entries: bundle-derived knobs the engine reads
    #    elsewhere or that have no matching config field. These are
    #    *not* written to the config but are surfaced to callers so
    #    the transparency panel can show "the bundle says X" even
    #    when X is not a knob ``apply_policy_bundle`` writes.
    # --------------------------------------------------------------
    overrides_applied.append(f"info: bundle.privacy_profile={bundle.privacy_profile}")
    overrides_applied.append(f"info: bundle.deep_run_profile={bundle.deep_run_profile}")
    overrides_applied.append(f"info: bundle.locality (Deep-Run reads at session start): {bundle.local_cloud_posture}")
    overrides_applied.append(f"info: bundle.cost_cap_usd hint (Deep-Run reads at session start): spend_profile={bundle.spend_profile}")
    overrides_applied.append(
        "info: scoring multipliers (no engine knob today): "
        f"exploration_ratio={bundle.exploration_ratio}, "
        f"persistence_strength={bundle.persistence_strength}, "
        f"goal_weighting={bundle.goal_weighting}, "
        f"drafting_aggressiveness={bundle.drafting_aggressiveness}"
    )
    overrides_applied.append(
        "info: prediction_horizon_bias (Deep-Run consumes at session start): "
        + ", ".join(f"{k}={v}" for k, v in bundle.prediction_horizon_bias.items())
    )

    # --------------------------------------------------------------
    # 5. Build the new config.
    # --------------------------------------------------------------
    config_updates: dict[str, Any] = {}
    if new_backend is not config.backend:
        config_updates["backend"] = new_backend
    if new_exploration is not config.exploration:
        config_updates["exploration"] = new_exploration
    if new_integrations is not config.integrations:
        config_updates["integrations"] = new_integrations

    # Always reflect the chosen bundle id on the policy block. This is
    # how the engine and downstream surfaces look up the bundle.
    if config.policy.selected_bundle_id != bundle.id:
        new_policy = config.policy.model_copy(update={"selected_bundle_id": bundle.id})
        config_updates["policy"] = new_policy
        overrides_applied.append(f"PolicyConfig.selected_bundle_id: {bundle.id}")

    new_config = config.model_copy(update=config_updates) if config_updates else config

    return AppliedPolicy(
        config=new_config,
        bundle_id=bundle.id,
        overrides_applied=tuple(overrides_applied),
    )


def _is_localhost_url(url: str) -> bool:
    """Return ``True`` for URLs that target the local machine.

    Used by the ``local_only`` endpoint filter. A URL is considered
    localhost when its host is ``localhost``, ``127.0.0.1``, ``::1``,
    or any ``127.x.x.x`` loopback address. Empty URLs are treated as
    *not* localhost — an empty endpoint is malformed and should be
    dropped under a strict local-only policy.
    """

    if not url:
        return False
    lowered = url.lower()
    # Quick string-prefix check before falling back to URL parsing.
    if "://" in lowered:
        # Strip scheme and split host:port from path.
        after_scheme = lowered.split("://", 1)[1]
        host = after_scheme.split("/", 1)[0].split("@", 1)[-1]
        host = host.split(":", 1)[0].strip("[]")
    else:
        host = lowered.split("/", 1)[0].split(":", 1)[0]
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    if host.startswith("127."):
        return True
    return False
