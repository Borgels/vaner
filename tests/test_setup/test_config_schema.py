# SPDX-License-Identifier: Apache-2.0
"""WS1 — VanerConfig schema tests (0.8.6).

Confirms the new ``[setup]`` and ``[policy]`` sections exist on
:class:`VanerConfig`, round-trip through Pydantic JSON, and that the
``IntentConfig.domain`` field is removed outright (no compat shim).
"""

from __future__ import annotations

import json
from pathlib import Path

from vaner.models.config import (
    IntentConfig,
    PolicyConfig,
    SetupConfig,
    VanerConfig,
)


def _make_config(tmp_path: Path) -> VanerConfig:
    return VanerConfig(
        repo_root=tmp_path,
        store_path=tmp_path / "store.db",
        telemetry_path=tmp_path / "telemetry.db",
    )


def test_vaner_config_includes_setup_and_policy_sections(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    assert isinstance(cfg.setup, SetupConfig)
    assert isinstance(cfg.policy, PolicyConfig)


def test_setup_defaults(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    assert cfg.setup.mode == "simple"
    assert cfg.setup.work_styles == ["mixed"]
    assert cfg.setup.priority == "balanced"
    assert cfg.setup.compute_posture == "balanced"
    assert cfg.setup.cloud_posture == "ask_first"
    assert cfg.setup.background_posture == "normal"
    assert cfg.setup.completed_at is None
    assert cfg.setup.version == 1


def test_policy_defaults(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    assert cfg.policy.selected_bundle_id == "hybrid_balanced"
    assert cfg.policy.bundle_overrides == {}
    assert cfg.policy.auto_select is True


def test_intent_config_no_longer_has_domain_field() -> None:
    # The 0.8.6 WS1 removal — verify the field is gone outright.
    assert "domain" not in IntentConfig.model_fields


def test_config_round_trips_through_json(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    payload = cfg.model_dump_json()
    parsed = json.loads(payload)
    assert "setup" in parsed
    assert "policy" in parsed
    # Reconstruct from the dumped JSON and verify equivalence.
    cfg2 = VanerConfig.model_validate_json(payload)
    assert cfg2.setup.mode == cfg.setup.mode
    assert cfg2.policy.selected_bundle_id == cfg.policy.selected_bundle_id


def test_setup_config_completed_at_accepts_datetime(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    when = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    cfg = SetupConfig(completed_at=when)
    payload = cfg.model_dump_json()
    parsed = SetupConfig.model_validate_json(payload)
    assert parsed.completed_at == when
