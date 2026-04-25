# SPDX-License-Identifier: Apache-2.0
"""WS7 — JSON serialisation helpers for the Setup surfaces (0.8.6).

WS6 (`vaner setup` CLI) and WS7 (`vaner.setup.*` MCP tools) both emit
the same JSON shapes. To keep them byte-identical we centralise the
``_bundle_to_dict``, ``_selection_to_dict``, ``_hardware_to_dict``, and
``_answers_from_payload`` helpers here and re-export them from
:mod:`vaner.cli.commands.setup` (where WS6 grew them in private form).

Importing the CLI module pulls in :mod:`typer` and :mod:`rich`, which
the MCP server should not depend on. Centralising the serialisers in
this leaf module keeps the MCP surface light and the CLI/MCP outputs
contractually identical.
"""

from __future__ import annotations

from typing import Any

from vaner.setup.answers import SetupAnswers
from vaner.setup.hardware import HardwareProfile
from vaner.setup.policy import VanerPolicyBundle
from vaner.setup.select import SelectionResult

__all__ = [
    "answers_from_payload",
    "bundle_to_dict",
    "hardware_to_dict",
    "selection_to_dict",
]


class AnswersValidationError(ValueError):
    """Raised by :func:`answers_from_payload` on a malformed payload.

    Distinct from :class:`ValueError` so MCP / HTTP callers can map it
    to a structured ``invalid_input`` response without catching every
    ValueError the underlying dataclass might emit.
    """


def bundle_to_dict(bundle: VanerPolicyBundle) -> dict[str, Any]:
    """Serialise a :class:`VanerPolicyBundle` to a JSON-safe dict."""

    return {
        "id": bundle.id,
        "label": bundle.label,
        "description": bundle.description,
        "local_cloud_posture": bundle.local_cloud_posture,
        "runtime_profile": bundle.runtime_profile,
        "spend_profile": bundle.spend_profile,
        "latency_profile": bundle.latency_profile,
        "privacy_profile": bundle.privacy_profile,
        "prediction_horizon_bias": dict(bundle.prediction_horizon_bias),
        "drafting_aggressiveness": bundle.drafting_aggressiveness,
        "exploration_ratio": bundle.exploration_ratio,
        "persistence_strength": bundle.persistence_strength,
        "goal_weighting": bundle.goal_weighting,
        "context_injection_default": bundle.context_injection_default,
        "deep_run_profile": bundle.deep_run_profile,
    }


def selection_to_dict(result: SelectionResult) -> dict[str, Any]:
    """Serialise a :class:`SelectionResult` for the recommend surface.

    The shape is the contract MCP wiring (WS7), the CLI's ``vaner setup
    recommend``, and desktop apps consume — keep it stable.
    """

    return {
        "bundle": bundle_to_dict(result.bundle),
        "score": result.score,
        "reasons": list(result.reasons),
        "runner_ups": [bundle_to_dict(b) for b in result.runner_ups],
        "forced_fallback": result.forced_fallback,
    }


def hardware_to_dict(hw: HardwareProfile) -> dict[str, Any]:
    """Serialise a :class:`HardwareProfile` for the hardware surface."""

    return {
        "os": hw.os,
        "cpu_class": hw.cpu_class,
        "ram_gb": hw.ram_gb,
        "gpu": hw.gpu,
        "gpu_vram_gb": hw.gpu_vram_gb,
        "is_battery": hw.is_battery,
        "thermal_constrained": hw.thermal_constrained,
        "detected_runtimes": list(hw.detected_runtimes),
        "detected_models": [list(row) for row in hw.detected_models],
        "tier": hw.tier,
    }


def answers_from_payload(raw: object) -> SetupAnswers:
    """Build a :class:`SetupAnswers` from a JSON-shaped dict.

    Accepts the public WS6 ``SetupAnswers`` shape: ``work_styles`` may
    be a single string or a list of strings; missing fields fall back
    to safe defaults (``priority='balanced'``, ``compute_posture='balanced'``,
    ``cloud_posture='ask_first'``, ``background_posture='normal'``).

    Raises :class:`AnswersValidationError` on a malformed payload (not
    a dict, ``work_styles`` not a list/string).
    """

    if not isinstance(raw, dict):
        raise AnswersValidationError("answers payload must be a JSON object")
    work_styles = raw.get("work_styles") or ["mixed"]
    if isinstance(work_styles, str):
        work_styles = [work_styles]
    if not isinstance(work_styles, list) or not all(isinstance(s, str) for s in work_styles):
        raise AnswersValidationError("work_styles must be a list of strings")
    return SetupAnswers(
        work_styles=tuple(work_styles),
        priority=str(raw.get("priority", "balanced")),  # type: ignore[arg-type]
        compute_posture=str(raw.get("compute_posture", "balanced")),  # type: ignore[arg-type]
        cloud_posture=str(raw.get("cloud_posture", "ask_first")),  # type: ignore[arg-type]
        background_posture=str(raw.get("background_posture", "normal")),  # type: ignore[arg-type]
    )
