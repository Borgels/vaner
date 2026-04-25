# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest
from pydantic import ValidationError

from vaner.models.config import (
    ContextInjectionConfig,
    IntegrationsConfig,
    VanerConfig,
)


def test_defaults_policy_hybrid() -> None:
    cfg = IntegrationsConfig()
    assert cfg.guidance_variant == "canonical"
    assert cfg.advertise_guidance_resource is True
    assert cfg.capability_detection_enabled is True
    assert cfg.context_injection.mode == "policy_hybrid"
    assert cfg.context_injection.digest_token_budget == 500
    assert cfg.context_injection.adopted_package_token_budget == 2000
    assert cfg.context_injection.max_context_fraction == 0.20
    assert cfg.context_injection.ttl_seconds == 600
    assert cfg.context_injection.include_provenance is True
    assert cfg.context_injection.include_confidence_details is False


def test_context_injection_mode_validation() -> None:
    with pytest.raises(ValidationError):
        ContextInjectionConfig(mode="bogus")  # type: ignore[arg-type]


def test_context_fraction_bounds() -> None:
    with pytest.raises(ValidationError):
        ContextInjectionConfig(max_context_fraction=0.0)
    with pytest.raises(ValidationError):
        ContextInjectionConfig(max_context_fraction=0.9)


def test_token_budget_bounds() -> None:
    with pytest.raises(ValidationError):
        ContextInjectionConfig(digest_token_budget=-1)
    with pytest.raises(ValidationError):
        ContextInjectionConfig(adopted_package_token_budget=99_999)


def test_vaner_config_has_integrations_default(tmp_path: Path) -> None:
    cfg = VanerConfig(
        repo_root=tmp_path,
        store_path=tmp_path / "store.db",
        telemetry_path=tmp_path / "telemetry.db",
    )
    assert cfg.integrations.context_injection.mode == "policy_hybrid"
