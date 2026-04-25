# SPDX-License-Identifier: Apache-2.0
"""Tests for ``vaner.setup.apply`` — WS5 policy-bundle application (0.8.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from vaner.models.config import (
    ExplorationConfig,
    ExplorationEndpoint,
    PolicyConfig,
    VanerConfig,
)
from vaner.setup.apply import (
    WIDENS_CLOUD_POSTURE_SENTINEL,
    AppliedPolicy,
    apply_policy_bundle,
)
from vaner.setup.catalog import PROFILE_CATALOG, bundle_by_id

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, **overrides) -> VanerConfig:
    base = {
        "repo_root": tmp_path / "repo",
        "store_path": tmp_path / "store.db",
        "telemetry_path": tmp_path / "telemetry.db",
    }
    base.update(overrides)
    return VanerConfig(**base)


# ---------------------------------------------------------------------------
# Pure-function contract
# ---------------------------------------------------------------------------


def test_pure_function(tmp_path: Path) -> None:
    """Same input -> same output, and original config is untouched."""

    cfg = _make_config(tmp_path)
    bundle = bundle_by_id("local_balanced")

    snapshot_prefer_local = cfg.backend.prefer_local
    snapshot_remote_budget = cfg.backend.remote_budget_per_hour
    snapshot_ci_mode = cfg.integrations.context_injection.mode
    snapshot_bundle_id = cfg.policy.selected_bundle_id

    out1 = apply_policy_bundle(cfg, bundle)
    out2 = apply_policy_bundle(cfg, bundle)

    # Original config unchanged.
    assert cfg.backend.prefer_local == snapshot_prefer_local
    assert cfg.backend.remote_budget_per_hour == snapshot_remote_budget
    assert cfg.integrations.context_injection.mode == snapshot_ci_mode
    assert cfg.policy.selected_bundle_id == snapshot_bundle_id

    # Same input -> same output.
    assert out1.bundle_id == out2.bundle_id == bundle.id
    assert out1.overrides_applied == out2.overrides_applied
    assert out1.config.backend.prefer_local == out2.config.backend.prefer_local
    assert out1.config.backend.remote_budget_per_hour == out2.config.backend.remote_budget_per_hour


# ---------------------------------------------------------------------------
# Cloud-widening guard
# ---------------------------------------------------------------------------


def test_bundle_widening_cloud_posture_flagged(tmp_path: Path) -> None:
    """Going from local_only -> hybrid must surface the WIDENS sentinel."""

    cfg = _make_config(
        tmp_path,
        policy=PolicyConfig(selected_bundle_id="local_lightweight"),
    )
    new_bundle = bundle_by_id("hybrid_balanced")

    result = apply_policy_bundle(cfg, new_bundle)

    flagged = [s for s in result.overrides_applied if s.startswith(WIDENS_CLOUD_POSTURE_SENTINEL)]
    assert len(flagged) == 1, f"expected exactly one widening sentinel, got: {result.overrides_applied}"
    assert "local_only" in flagged[0]
    assert "hybrid" in flagged[0]


def test_no_widening_when_same_posture(tmp_path: Path) -> None:
    """Applying the same bundle twice does not flag widening."""

    cfg = _make_config(
        tmp_path,
        policy=PolicyConfig(selected_bundle_id="hybrid_balanced"),
    )
    bundle = bundle_by_id("hybrid_balanced")

    result = apply_policy_bundle(cfg, bundle)

    flagged = [s for s in result.overrides_applied if s.startswith(WIDENS_CLOUD_POSTURE_SENTINEL)]
    assert flagged == []


def test_no_widening_when_narrowing(tmp_path: Path) -> None:
    """Going from cloud_preferred -> local_only must NOT flag widening."""

    cfg = _make_config(
        tmp_path,
        policy=PolicyConfig(selected_bundle_id="hybrid_quality"),  # cloud_preferred
    )
    new_bundle = bundle_by_id("local_lightweight")  # local_only

    result = apply_policy_bundle(cfg, new_bundle)

    flagged = [s for s in result.overrides_applied if s.startswith(WIDENS_CLOUD_POSTURE_SENTINEL)]
    assert flagged == []


# ---------------------------------------------------------------------------
# Per-bundle smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bundle", PROFILE_CATALOG, ids=[b.id for b in PROFILE_CATALOG])
def test_seven_bundles_each_apply_cleanly(tmp_path: Path, bundle) -> None:
    """Every catalogued bundle applies without raising and yields an AppliedPolicy."""

    cfg = _make_config(tmp_path)

    result = apply_policy_bundle(cfg, bundle)

    assert isinstance(result, AppliedPolicy)
    assert result.bundle_id == bundle.id
    assert result.config.policy.selected_bundle_id == bundle.id
    assert result.config.backend.prefer_local == (bundle.local_cloud_posture in ("local_only", "local_preferred"))
    # remote_budget_per_hour matches the bundle's spend-profile band.
    expected_budgets = {"zero": 0, "low": 30, "medium": 60, "high": 120}
    assert result.config.backend.remote_budget_per_hour == expected_budgets[bundle.spend_profile]


# ---------------------------------------------------------------------------
# User overrides
# ---------------------------------------------------------------------------


def test_user_override_wins_over_bundle_default(tmp_path: Path) -> None:
    """User's context_injection_mode override beats the bundle default."""

    cfg = _make_config(tmp_path)
    bundle = bundle_by_id("local_lightweight")  # bundle default = digest_only

    # User's preferred mode is none — and they keep it across bundle changes.
    result = apply_policy_bundle(
        cfg,
        bundle,
        user_overrides={"context_injection_mode": "none"},
    )

    assert result.config.integrations.context_injection.mode == "none"
    # No line writing the bundle default in the audit list — the user
    # override wins.
    assert not any(
        f"context_injection.mode: {bundle.context_injection_default}" == s
        or s.endswith(f"context_injection.mode: {bundle.context_injection_default}")
        for s in result.overrides_applied
    )


# ---------------------------------------------------------------------------
# Default bundle is a no-op against a fresh config
# ---------------------------------------------------------------------------


def test_no_op_for_default_hybrid_balanced(tmp_path: Path) -> None:
    """Applying hybrid_balanced (the default) to a fresh VanerConfig is
    safe — no widening sentinel, no autonomy-expanding flags, and the
    only mutations are the documented bundle-default writes.

    The bundle's ``low`` spend profile and ``hybrid`` posture do not
    perfectly mirror the bare :class:`BackendConfig` defaults
    (``prefer_local=True``, ``remote_budget_per_hour=60``); applying
    the default bundle nudges those toward the bundle's outcome
    semantics. The assertion is that the diff is bounded and
    explainable, not that it is empty.
    """

    cfg = _make_config(tmp_path)
    bundle = bundle_by_id("hybrid_balanced")

    result = apply_policy_bundle(cfg, bundle)

    # No widening — same posture as the prior selection.
    flagged = [s for s in result.overrides_applied if s.startswith(WIDENS_CLOUD_POSTURE_SENTINEL)]
    assert flagged == []

    # Default bundle id stays the same -> no PolicyConfig mutation line.
    assert result.config.policy.selected_bundle_id == bundle.id
    assert not any(s.startswith("PolicyConfig.") for s in result.overrides_applied)

    # Allowed mutation lines are exactly the documented BackendConfig
    # tweaks. No ExplorationConfig / IntegrationsConfig writes.
    backend_mutations = [s for s in result.overrides_applied if s.startswith("BackendConfig.")]
    assert all(
        s.startswith("BackendConfig.prefer_local") or s.startswith("BackendConfig.remote_budget_per_hour") for s in backend_mutations
    ), backend_mutations

    assert not any(s.startswith("ExplorationConfig.") for s in result.overrides_applied)
    assert not any(s.startswith("IntegrationsConfig.") for s in result.overrides_applied)


# ---------------------------------------------------------------------------
# Endpoint filter under local_only
# ---------------------------------------------------------------------------


def test_local_only_filters_remote_endpoints(tmp_path: Path) -> None:
    """A local_only bundle drops non-localhost exploration endpoints."""

    cfg = _make_config(
        tmp_path,
        exploration=ExplorationConfig(
            endpoints=[
                ExplorationEndpoint(url="http://127.0.0.1:8000/v1", model="local-a"),
                ExplorationEndpoint(url="https://api.openai.com/v1", model="gpt-4o"),
                ExplorationEndpoint(url="http://localhost:11434", model="ollama-tag"),
            ],
        ),
    )
    bundle = bundle_by_id("local_lightweight")  # local_only

    result = apply_policy_bundle(cfg, bundle)

    kept_urls = [ep.url for ep in result.config.exploration.endpoints]
    assert "https://api.openai.com/v1" not in kept_urls
    assert "http://127.0.0.1:8000/v1" in kept_urls
    assert "http://localhost:11434" in kept_urls
    # Original config is untouched.
    assert len(cfg.exploration.endpoints) == 3


# ---------------------------------------------------------------------------
# Apply does not cross prepare/promote/adopt/execute boundary
# ---------------------------------------------------------------------------


def test_apply_no_autonomy(tmp_path: Path) -> None:
    """``apply_policy_bundle`` must never set knobs that cross the
    prepare/promote/adopt/execute boundary.

    Concretely: we assert the function does not invent ``auto_adopt``
    / ``auto_execute`` flags on any config block, does not write a
    ``cost_cap_usd`` or any Deep-Run session field, and does not
    mutate fields outside the documented set.
    """

    cfg = _make_config(tmp_path)
    # Try every bundle — each must respect the boundary.
    forbidden_substrings = (
        "auto_adopt",
        "auto_execute",
        "auto_apply",
        "cost_cap_usd",
        "DeepRunSession",
    )
    for bundle in PROFILE_CATALOG:
        result = apply_policy_bundle(cfg, bundle)
        for line in result.overrides_applied:
            # ``info:`` lines may *mention* spend_profile or
            # cost_cap_usd as a hint; mutation lines must not.
            if line.startswith("info:"):
                continue
            for needle in forbidden_substrings:
                assert needle not in line, f"forbidden token {needle!r} in mutation line: {line}"
        # Engine-knob fields the function is allowed to touch are a
        # closed set — verify nothing else changed by sampling.
        cfg_dump = result.config.model_dump()
        baseline_dump = cfg.model_dump()
        # Top-level blocks the function may rewrite.
        allowed_blocks = {"backend", "exploration", "integrations", "policy"}
        for block_name in baseline_dump:
            if block_name in allowed_blocks:
                continue
            assert cfg_dump[block_name] == baseline_dump[block_name], f"bundle {bundle.id} unexpectedly mutated config block {block_name!r}"


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


def test_engine_init_applies_bundle(tmp_path: Path) -> None:
    """The engine materialises the selected bundle at init time."""

    from vaner.engine import VanerEngine
    from vaner.intent.adapter import CodeRepoAdapter

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def hi(): return 'hi'\n")

    async def _stub_llm(_prompt: str) -> str:
        return '{"ranked_files": [], "semantic_intent": "", "confidence": 0.0, "follow_on": []}'

    engine = VanerEngine(adapter=CodeRepoAdapter(repo), llm=_stub_llm)
    # Default bundle is "hybrid_balanced" — applied policy is non-None
    # and reflects the selected id.
    assert engine._applied_policy is not None  # noqa: SLF001
    assert engine._applied_policy.bundle_id == "hybrid_balanced"  # noqa: SLF001

    # Now flip the selected bundle and re-refresh; the engine picks it up.
    engine.config.policy = PolicyConfig(selected_bundle_id="local_lightweight")
    engine._refresh_policy_bundle_state()  # noqa: SLF001

    assert engine._applied_policy.bundle_id == "local_lightweight"  # noqa: SLF001
    # local_lightweight is local_only -> prefer_local True, remote_budget 0.
    assert engine._applied_policy.config.backend.prefer_local is True  # noqa: SLF001
    assert engine._applied_policy.config.backend.remote_budget_per_hour == 0  # noqa: SLF001


def test_engine_unknown_bundle_id_falls_back_safely(tmp_path: Path) -> None:
    """An unknown bundle id is logged and the engine continues without applied policy."""

    from vaner.engine import VanerEngine
    from vaner.intent.adapter import CodeRepoAdapter

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def hi(): return 'hi'\n")

    async def _stub_llm(_prompt: str) -> str:
        return '{"ranked_files": [], "semantic_intent": "", "confidence": 0.0, "follow_on": []}'

    engine = VanerEngine(adapter=CodeRepoAdapter(repo), llm=_stub_llm)
    engine.config.policy = PolicyConfig(selected_bundle_id="this_does_not_exist")
    engine._refresh_policy_bundle_state()  # noqa: SLF001

    # No raise; applied_policy is None.
    assert engine._applied_policy is None  # noqa: SLF001
