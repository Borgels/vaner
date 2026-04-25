# SPDX-License-Identifier: Apache-2.0
"""WS1 — Setup primitives: enums (0.8.6).

These ``Literal`` type aliases are the single source of truth for the
outcome-level question vocabulary surfaced by Simple Mode (CLI wizard,
desktop apps, MCP/HTTP setup tools). Each alias matches one question in
the five-step Simple-Mode flow described in the 0.8.6 spec §5:

- ``WorkStyle`` — multi-select. What kind of work do you want help with?
  ("writing", "research", "planning", "support", "learning", "coding",
  "general", "mixed", "unsure"). The engine averages priors when more
  than one work style is selected.
- ``Priority`` — single-select. What matters most? ("balanced", "speed",
  "quality", "privacy", "cost", "low_resource").
- ``ComputePosture`` — single-select. How hard should this machine work
  for you? ("light", "balanced", "available_power").
- ``CloudPosture`` — single-select. How do you feel about cloud LLMs?
  ("local_only", "ask_first", "hybrid_when_worth_it",
  "best_available").
- ``BackgroundPosture`` — single-select. How aggressive should
  background pondering be? ("minimal", "normal", "idle_more",
  "deep_run_aggressive").
- ``HardwareTier`` — output of WS2's ``tier_for()`` mapping. Declared
  here so all of WS1+WS2+WS3 share one definition.

We use ``Literal`` aliases rather than ``enum.Enum`` classes to match the
existing codebase pattern (see ``DeepRunPreset`` / ``DeepRunFocus`` /
``DeepRunHorizonBias`` in :mod:`vaner.intent.deep_run`). The aliases
participate naturally in Pydantic validation, JSON serialisation, and
``ts-rs`` codegen on the ``vaner-contract`` side.
"""

from __future__ import annotations

from typing import Literal

WorkStyle = Literal[
    "writing",
    "research",
    "planning",
    "support",
    "learning",
    "coding",
    "general",
    "mixed",
    "unsure",
]

Priority = Literal[
    "balanced",
    "speed",
    "quality",
    "privacy",
    "cost",
    "low_resource",
]

ComputePosture = Literal[
    "light",
    "balanced",
    "available_power",
]

CloudPosture = Literal[
    "local_only",
    "ask_first",
    "hybrid_when_worth_it",
    "best_available",
]

BackgroundPosture = Literal[
    "minimal",
    "normal",
    "idle_more",
    "deep_run_aggressive",
]

# Declared here for shared use across WS1 (config schema) and WS2
# (hardware detection / tier_for()). WS2 owns the mapping logic.
HardwareTier = Literal[
    "light",
    "capable",
    "high_performance",
    "unknown",
]


__all__ = [
    "BackgroundPosture",
    "CloudPosture",
    "ComputePosture",
    "HardwareTier",
    "Priority",
    "WorkStyle",
]
