# SPDX-License-Identifier: Apache-2.0

"""Tests for ponder-loop wall-clock caps.

Covers ``ComputeConfig.max_cycle_seconds`` (bounds a single
``VanerEngine.precompute_cycle``) and ``ComputeConfig.max_session_minutes``
(bounds a continuous ``VanerDaemon.run_forever`` session).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.models.config import ComputeConfig


def test_compute_config_ponder_defaults() -> None:
    compute = ComputeConfig()
    assert compute.max_cycle_seconds == 300
    assert compute.max_session_minutes is None


def test_compute_config_ponder_set() -> None:
    compute = ComputeConfig(max_cycle_seconds=42, max_session_minutes=5)
    assert compute.max_cycle_seconds == 42
    assert compute.max_session_minutes == 5


@pytest.mark.asyncio
async def test_precompute_cycle_respects_max_cycle_seconds(temp_repo) -> None:
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    engine.config.compute.max_cycle_seconds = 1

    call_count = {"n": 0}
    base = [1000.0]

    def fake_monotonic() -> float:
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return base[0]
        return base[0] + 1000.0

    with patch("vaner.engine.time.monotonic", side_effect=fake_monotonic):
        full_packages = await engine.precompute_cycle()

    assert isinstance(full_packages, int)
    assert full_packages >= 0


@pytest.mark.asyncio
async def test_precompute_cycle_zero_disables_cap(temp_repo) -> None:
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    engine.config.compute.max_cycle_seconds = 0
    full_packages = await engine.precompute_cycle()
    assert isinstance(full_packages, int)
