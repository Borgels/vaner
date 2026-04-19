# SPDX-License-Identifier: Apache-2.0
"""Exploration policy (legacy stub).

The epsilon-greedy ``ExplorationPolicy`` has been superseded by
``ScoringPolicy`` in ``vaner.intent.scoring_policy``, which is the actual
decision surface for the exploration frontier.

This module is retained only as a backward-compatibility shim so that any
external code that imports ``ExplorationPolicy`` does not immediately break.
The ``choose()`` method is a no-op wrapper; call sites should migrate to
``ScoringPolicy.compute_score()`` instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ExplorationPolicy:
    """Deprecated — use ``ScoringPolicy`` instead."""

    strategy: str = "passthrough"
    epsilon: float = 0.0
    min_epsilon: float = 0.0
    exploration_decay: float = 1.0

    def set_phase(self, phase: str) -> None:  # noqa: ARG002
        """No-op: phase is now consumed by ``ScoringPolicy`` directly."""

    def choose(self, predictions: list[Any], *, top_k: int) -> list[Any]:
        """Return top-k predictions in score order (no randomisation)."""
        if not predictions:
            return []
        ordered = sorted(predictions, key=lambda item: item.score, reverse=True)
        return ordered[:top_k]
