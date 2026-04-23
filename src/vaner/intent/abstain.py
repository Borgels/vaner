# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AbstentionPolicy:
    """Decide whether the ponder cycle should abstain from drilling a hypothesis.

    Abstain when the posterior is too flat (high normalised entropy) AND the
    contradiction signal is high.  Either condition alone is insufficient —
    flat-but-uncontradicted posteriors just mean "early session, no strong
    prior yet", while contradicted-but-peaked posteriors mean "conflicting
    evidence around one dominant hypothesis" (still worth drilling).

    Set ``contradiction_threshold`` to a value >= 1.0 to disable the
    contradiction gate entirely, reducing the policy to an entropy-only check.
    Since contradiction scores are in [0, 1], a threshold of exactly 1.0 is
    effectively unreachable and acts as a clean disable sentinel.
    This is the recommended setting when a contradiction signal is not yet
    available in the calling context.
    """

    entropy_threshold: float = 0.85
    contradiction_threshold: float = 0.6

    def should_abstain(
        self,
        posterior: dict[str, float],
        *,
        contradiction_score: float = 0.0,
    ) -> bool:
        n = len(posterior)
        if n < 2:
            return False
        total = sum(max(0.0, float(v)) for v in posterior.values())
        if total <= 0.0:
            return False
        probs = [max(0.0, float(v)) / total for v in posterior.values() if v > 0.0]
        if not probs:
            return False
        entropy = -sum(p * math.log(p) for p in probs if p > 0.0)
        max_entropy = math.log(n)
        if max_entropy <= 0.0:
            return False
        normalised_entropy = entropy / max_entropy
        entropy_exceeded = normalised_entropy > self.entropy_threshold
        # Contradiction gate: disabled when threshold >= 1.0 (scores are 0..1).
        contradiction_exceeded = self.contradiction_threshold >= 1.0 or contradiction_score > self.contradiction_threshold
        return entropy_exceeded and contradiction_exceeded
