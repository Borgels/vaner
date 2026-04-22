# SPDX-License-Identifier: Apache-2.0
"""Activity timing model — how much compute does Vaner have before the next prompt?

Vaner pre-computes context and draft responses on the assumption that the
developer will keep prompting. The *quality* of that bet depends on timing:

- If the user typically prompts every ~20s when actively engaged, spending 5
  minutes deep-drilling a single high-priority line wastes budget on stale
  predictions — the next prompt arrives and the drill didn't finish.
- If the user just paused (last prompt 4 minutes ago, typical gap 30s), they
  might still be reading a response or they might have stepped away; Vaner
  should ramp down gracefully rather than burn cycles on cold predictions.
- If the user comes back after 10 minutes of idle, Vaner has earned a full
  ponder cycle — enough time to drill deep on the top prediction and widen
  coverage instead of bailing early.

This module distils ``query_history`` timestamps into an inter-prompt-gap
EMA plus a simple next-prompt ETA estimator. The engine consults it at the
start of every precompute cycle to set an adaptive deadline.

The model is intentionally cheap to evaluate (O(n) over the last ~50 history
rows) and deterministic — it is a *prior*, not a predictor. If no history is
available it reports ``None`` and callers fall back to the static
``compute.max_cycle_seconds`` budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TimingObservation:
    """Snapshot of the inter-prompt timing model at a point in time.

    All fields are seconds. ``None`` means insufficient evidence.
    """

    mean_gap_seconds: float | None = None
    ema_gap_seconds: float | None = None
    last_prompt_age_seconds: float | None = None
    estimated_seconds_until_next_prompt: float | None = None
    active_session: bool = False
    sample_count: int = 0


@dataclass
class ActivityTimingModel:
    """EMA-based estimator of inter-prompt cadence.

    Parameters
    ----------
    ema_alpha:
        EMA smoothing factor applied to each new inter-prompt gap. A higher
        alpha makes the model react faster to changes in cadence; the default
        ``0.35`` trades off noise rejection against responsiveness.
    active_session_gap_seconds:
        Maximum gap that still counts as *active session*. Gaps longer than
        this (e.g. overnight breaks) are excluded from the EMA so a long AFK
        doesn't pollute the model's notion of "typical" active cadence.
    max_history_samples:
        Upper bound on how many history rows the engine passes in — pure
        guard against degenerate O(n²) behaviour on pathological stores.
    min_gap_floor_seconds / max_gap_cap_seconds:
        Clamp the ETA returned to callers so adaptive budgeting never
        degenerates to 0 or to an absurdly long ponder.
    """

    ema_alpha: float = 0.35
    active_session_gap_seconds: float = 180.0  # 3 minutes
    max_history_samples: int = 50
    min_gap_floor_seconds: float = 3.0
    max_gap_cap_seconds: float = 900.0  # 15 minutes

    _ema: float | None = field(default=None, init=False)
    _last_prompt_ts: float | None = field(default=None, init=False)
    _sample_count: int = field(default=0, init=False)
    _mean_gap: float | None = field(default=None, init=False)

    def reset(self) -> None:
        self._ema = None
        self._last_prompt_ts = None
        self._sample_count = 0
        self._mean_gap = None

    def rebuild_from_history(self, timestamps: list[float]) -> None:
        """Seed the model from a sorted (oldest → newest) list of timestamps.

        Non-monotonic or malformed inputs are filtered. Gaps longer than
        ``active_session_gap_seconds`` are treated as session boundaries —
        the EMA resumes from the next sample but the gap itself is ignored
        so idle periods don't dominate the cadence estimate.
        """
        self.reset()
        sanitized: list[float] = []
        for ts in timestamps[-self.max_history_samples :]:
            try:
                value = float(ts)
            except (TypeError, ValueError):
                continue
            if value <= 0.0:
                continue
            if sanitized and value < sanitized[-1]:
                # Non-monotonic: drop out-of-order sample rather than
                # contaminating the EMA with a negative gap.
                continue
            sanitized.append(value)
        if not sanitized:
            return
        self._last_prompt_ts = sanitized[-1]
        gaps: list[float] = []
        for prev, curr in zip(sanitized, sanitized[1:], strict=False):
            gap = curr - prev
            if gap <= 0.0:
                continue
            if gap > self.active_session_gap_seconds:
                # Session boundary — don't fold this into the EMA.
                continue
            gaps.append(gap)
            if self._ema is None:
                self._ema = gap
            else:
                self._ema = self.ema_alpha * gap + (1.0 - self.ema_alpha) * self._ema
        if gaps:
            self._mean_gap = sum(gaps) / len(gaps)
            self._sample_count = len(gaps)

    def record_prompt(self, timestamp: float | None = None) -> None:
        """Fold a newly arrived prompt timestamp into the model."""
        ts = float(timestamp) if timestamp is not None else time.time()
        if self._last_prompt_ts is not None and ts > self._last_prompt_ts:
            gap = ts - self._last_prompt_ts
            if 0.0 < gap <= self.active_session_gap_seconds:
                if self._ema is None:
                    self._ema = gap
                else:
                    self._ema = self.ema_alpha * gap + (1.0 - self.ema_alpha) * self._ema
                self._sample_count += 1
                # Running mean without materialising the list.
                if self._mean_gap is None:
                    self._mean_gap = gap
                else:
                    self._mean_gap = self._mean_gap + (gap - self._mean_gap) / self._sample_count
        self._last_prompt_ts = ts

    def observe(self, now: float | None = None) -> TimingObservation:
        """Return the current timing snapshot.

        The ``estimated_seconds_until_next_prompt`` field is a *residual*
        estimate — EMA minus age of the last prompt — clamped to
        ``[min_gap_floor_seconds, max_gap_cap_seconds]``. When the user has
        been idle longer than the active-session threshold the ``active_session``
        flag is False and the residual is saturated at the upper cap, so
        callers can treat the next cycle as "plenty of time to explore".
        """
        current = float(now) if now is not None else time.time()
        age: float | None = None
        if self._last_prompt_ts is not None:
            age = max(0.0, current - self._last_prompt_ts)
        active = age is not None and age <= self.active_session_gap_seconds
        eta: float | None = None
        if self._ema is not None and age is not None:
            residual = self._ema - age
            if not active:
                residual = self.max_gap_cap_seconds
            eta = max(self.min_gap_floor_seconds, min(self.max_gap_cap_seconds, residual))
        return TimingObservation(
            mean_gap_seconds=self._mean_gap,
            ema_gap_seconds=self._ema,
            last_prompt_age_seconds=age,
            estimated_seconds_until_next_prompt=eta,
            active_session=active,
            sample_count=self._sample_count,
        )

    def budget_seconds_for_cycle(
        self,
        *,
        hard_cap_seconds: float,
        soft_min_seconds: float = 5.0,
        utilisation_fraction: float = 0.8,
        now: float | None = None,
    ) -> float:
        """Derive an adaptive wall-clock budget for the next precompute cycle.

        Returns a value in ``[soft_min_seconds, hard_cap_seconds]``. The
        engine caps this further by ``compute.max_cycle_seconds`` so operators
        always retain an upper bound.

        The idea is to burn ~``utilisation_fraction`` of the *expected* time
        until the next prompt so Vaner finishes one exploration phase before
        the user arrives. During active sessions this keeps cycles snappy
        (tens of seconds); when idle, it expands cycles up to ``hard_cap_seconds``.
        """
        observation = self.observe(now=now)
        eta = observation.estimated_seconds_until_next_prompt
        if eta is None:
            return hard_cap_seconds
        derived = eta * max(0.1, min(1.0, utilisation_fraction))
        if not observation.active_session:
            return hard_cap_seconds
        return max(soft_min_seconds, min(hard_cap_seconds, derived))
