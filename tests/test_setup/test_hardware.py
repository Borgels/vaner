"""Tests for vaner.setup.hardware.

All probes are mocked so the tests run identically on any platform / CI.
A single optional smoke test (``test_real_detect_smoke``) exercises the real
machine when ``VANER_HW_REAL`` is set.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from typing import Any
from unittest.mock import patch

import pytest

from vaner.setup import hardware as hw


def _make_profile(**overrides: Any) -> hw.HardwareProfile:
    """Build a HardwareProfile with sane defaults for tier_for tests."""
    base: dict[str, Any] = {
        "os": "linux",
        "cpu_class": "mid",
        "ram_gb": 16,
        "gpu": "integrated",
        "gpu_vram_gb": None,
        "is_battery": False,
        "thermal_constrained": False,
        "detected_runtimes": (),
        "detected_models": (),
        "tier": "unknown",
    }
    base.update(overrides)
    profile = hw.HardwareProfile(**base)
    return dataclasses.replace(profile, tier=hw.tier_for(profile))


# ---------------------------------------------------------------------------
# tier_for() table
# ---------------------------------------------------------------------------


def test_tier_unknown_when_os_missing() -> None:
    """detect() routes through tier="unknown" when probes fail entirely."""
    with (
        patch.object(hw, "_probe_os", return_value=None),
        patch.object(hw, "_probe_cpu_and_ram", return_value=("low", 0)),
        patch.object(hw, "_probe_gpu", return_value=("none", None)),
        patch.object(hw, "_probe_battery", return_value=False),
        patch.object(hw, "_probe_thermal", return_value=False),
        patch.object(hw, "_probe_runtimes", return_value=()),
        patch.object(hw, "_probe_models", return_value=()),
    ):
        profile = hw.detect()
    assert profile.tier == "unknown"


def test_tier_light_low_ram_no_gpu() -> None:
    profile = _make_profile(ram_gb=8, gpu="none", cpu_class="low")
    assert profile.tier == "light"


def test_tier_high_perf_workstation() -> None:
    profile = _make_profile(
        ram_gb=64,
        gpu="nvidia",
        gpu_vram_gb=24,
        cpu_class="high",
        is_battery=False,
    )
    assert profile.tier == "high_performance"


def test_tier_capable_default() -> None:
    profile = _make_profile(
        ram_gb=32,
        gpu="integrated",
        gpu_vram_gb=None,
        cpu_class="mid",
        is_battery=False,
    )
    assert profile.tier == "capable"


def test_battery_low_ram_demotes_to_light() -> None:
    """16GB on battery is below the 24GB battery cutoff → light."""
    profile = _make_profile(
        ram_gb=16,
        gpu="integrated",
        cpu_class="mid",
        is_battery=True,
    )
    assert profile.tier == "light"


def test_apple_silicon_unified_memory() -> None:
    """Apple Silicon reports VRAM as None and still maps to high_performance."""
    profile = _make_profile(
        os="darwin",
        ram_gb=64,
        gpu="apple_silicon",
        gpu_vram_gb=None,
        cpu_class="high",
        is_battery=False,
    )
    assert profile.tier == "high_performance"


def test_apple_silicon_on_battery_blocks_high_perf() -> None:
    """Battery flag is dispositive: even Apple Silicon laptops drop tier."""
    profile = _make_profile(
        os="darwin",
        ram_gb=32,
        gpu="apple_silicon",
        gpu_vram_gb=None,
        cpu_class="high",
        is_battery=True,
    )
    # 32GB ≥ 24GB battery cutoff → not light, but battery blocks high_perf.
    assert profile.tier == "capable"


def test_tier_for_unknown_zero_ram() -> None:
    profile = hw.HardwareProfile(
        os="linux",
        cpu_class="low",
        ram_gb=0,
        gpu="none",
        gpu_vram_gb=None,
        is_battery=False,
        thermal_constrained=False,
        detected_runtimes=(),
        detected_models=(),
        tier="unknown",
    )
    assert hw.tier_for(profile) == "unknown"


# ---------------------------------------------------------------------------
# Probe fail-safety
# ---------------------------------------------------------------------------


def test_runtime_probe_fails_safe() -> None:
    """subprocess timeout inside a runtime probe must not bubble up."""
    err = subprocess.TimeoutExpired(cmd="anything", timeout=1)
    with (
        patch.object(hw.shutil, "which", return_value=None),
        patch.object(hw, "_http_get_ok", return_value=(False, None)),
        patch.object(hw.subprocess, "run", side_effect=err),
    ):
        result = hw._probe_runtimes()
    assert result == ()


def test_ollama_http_probe_returns_404() -> None:
    """A non-200 from /api/tags + no binary on PATH means ollama not detected."""
    with (
        patch.object(hw.shutil, "which", return_value=None),
        patch.object(hw, "_http_get_ok", return_value=(False, None)),
    ):
        assert hw._probe_ollama() is False


def test_ollama_detected_when_binary_present() -> None:
    with patch.object(hw.shutil, "which", return_value="/usr/bin/ollama"):
        assert hw._probe_ollama() is True


def test_lmstudio_detected_via_http() -> None:
    with patch.object(hw, "_http_get_ok", return_value=(True, '{"data":[]}')):
        assert hw._probe_lmstudio() is True


def test_models_ollama_parses_size() -> None:
    body = '{"models":[{"name":"qwen3:8b","size":5368709120}]}'
    with patch.object(hw, "_http_get_ok", return_value=(True, body)):
        rows = hw._probe_models_ollama()
    assert rows == [("ollama", "qwen3:8b", "5.0GB")]


def test_models_ollama_handles_bad_json() -> None:
    with patch.object(hw, "_http_get_ok", return_value=(True, "not json")):
        assert hw._probe_models_ollama() == []


def test_models_lmstudio_parses_ids() -> None:
    body = '{"data":[{"id":"llama-3-8b"},{"id":"mistral-7b"}]}'
    with patch.object(hw, "_http_get_ok", return_value=(True, body)):
        rows = hw._probe_models_lmstudio()
    assert rows == [
        ("lmstudio", "llama-3-8b", "unknown"),
        ("lmstudio", "mistral-7b", "unknown"),
    ]


def test_thermal_probe_returns_false_off_linux() -> None:
    with patch.object(hw.sys, "platform", "darwin"):
        assert hw._probe_thermal() is False


def test_battery_probe_no_psutil_no_sys() -> None:
    """No psutil, no /sys/class/power_supply, not macOS → False."""
    with (
        patch.dict("sys.modules", {"psutil": None}),
        patch.object(hw.sys, "platform", "win32"),
    ):
        assert hw._probe_battery() is False


def test_gpu_probe_handles_subprocess_failure() -> None:
    """Combined GPU probe must never raise."""
    err = subprocess.TimeoutExpired(cmd="lspci", timeout=1)
    with (
        patch.object(hw, "_probe_gpu_nvidia", return_value=None),
        patch.object(hw.shutil, "which", return_value="/usr/bin/lspci"),
        patch.object(hw.subprocess, "run", side_effect=err),
        patch.object(hw.sys, "platform", "linux"),
    ):
        gpu, vram = hw._probe_gpu()
    assert gpu == "none"
    assert vram is None


# ---------------------------------------------------------------------------
# detect() composition
# ---------------------------------------------------------------------------


def test_detect_returns_frozen_profile() -> None:
    """detect() returns an immutable dataclass."""
    with (
        patch.object(hw, "_probe_os", return_value="linux"),
        patch.object(hw, "_probe_cpu_and_ram", return_value=("mid", 16)),
        patch.object(hw, "_probe_gpu", return_value=("integrated", None)),
        patch.object(hw, "_probe_battery", return_value=False),
        patch.object(hw, "_probe_thermal", return_value=False),
        patch.object(hw, "_probe_runtimes", return_value=()),
        patch.object(hw, "_probe_models", return_value=()),
    ):
        profile = hw.detect()
    assert profile.tier == "capable"
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.ram_gb = 99  # type: ignore[misc]


def test_detect_threads_runtimes_into_models_probe() -> None:
    """The set of detected runtimes must be passed into _probe_models."""
    captured: list[Any] = []

    def fake_models(runtimes: Any) -> tuple[tuple[str, str, str], ...]:
        captured.append(runtimes)
        return (("ollama", "qwen3:8b", "5.0GB"),)

    with (
        patch.object(hw, "_probe_os", return_value="linux"),
        patch.object(hw, "_probe_cpu_and_ram", return_value=("high", 64)),
        patch.object(hw, "_probe_gpu", return_value=("nvidia", 24)),
        patch.object(hw, "_probe_battery", return_value=False),
        patch.object(hw, "_probe_thermal", return_value=False),
        patch.object(hw, "_probe_runtimes", return_value=("ollama",)),
        patch.object(hw, "_probe_models", side_effect=fake_models),
    ):
        profile = hw.detect()
    assert captured == [("ollama",)]
    assert profile.detected_models == (("ollama", "qwen3:8b", "5.0GB"),)
    assert profile.tier == "high_performance"


# ---------------------------------------------------------------------------
# Optional CI smoke test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("VANER_HW_REAL"),
    reason="real hardware probe smoke test (set VANER_HW_REAL=1 to enable)",
)
def test_real_detect_smoke() -> None:
    profile = hw.detect()
    assert profile.tier in {"light", "capable", "high_performance", "unknown"}
