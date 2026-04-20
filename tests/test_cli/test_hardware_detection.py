# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from vaner.cli.commands import app as app_module

if not hasattr(app_module, "_detect_nvidia_smi_profile"):
    pytest.skip("nvidia-smi hardware fallback helper unavailable on this branch surface", allow_module_level=True)


def test_detect_nvidia_smi_profile_parses_memory(monkeypatch) -> None:
    monkeypatch.setattr(app_module.shutil, "which", lambda _name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        app_module.subprocess,
        "check_output",
        lambda *_args, **_kwargs: "NVIDIA GeForce RTX 5090, 32607\nNVIDIA GeForce RTX 4080, 16376\n",
    )
    profile = app_module._detect_nvidia_smi_profile()
    assert profile is not None
    assert profile["device"] == "cuda"
    assert profile["gpu_count"] == 2
    assert profile["vram_gb"] == 31.8


def test_detect_hardware_profile_falls_back_to_nvidia_smi(monkeypatch) -> None:
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_properties=lambda _idx: SimpleNamespace(total_memory=0),
        ),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(app_module, "_detect_nvidia_smi_profile", lambda: {"device": "cuda", "gpu_count": 1, "vram_gb": 31.8})
    profile = app_module._detect_hardware_profile()
    assert profile["device"] == "cuda"
    assert profile["gpu_count"] == 1
    assert profile["vram_gb"] == 31.8
