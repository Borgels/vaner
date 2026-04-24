# SPDX-License-Identifier: Apache-2.0
"""WS4 — Prediction adoption-outcome data model (0.8.4).

Captures what happens to a prediction *after* a user / agent adopts it.
A pending outcome is written at adoption time; a later sweep resolves it
to one of:

- ``confirmed``: ``adoption_pending_confirm_cycles`` cycles have
  elapsed with no contradicting signal. Treated as "the user used this
  and didn't walk it back."
- ``rejected``: the adopted prediction's anchor was invalidated
  (file_change / commit / progress_reconciled) or a
  ``rollback_kept_maturation()`` fired on the same prediction inside
  its probation window. Treated as "the user adopted this but it
  turned out wrong."
- ``stale``: the prediction was staled by an ordinary invalidation
  signal (e.g. its underlying files changed) before the pending window
  closed. Treated as neutral — the user's choice wasn't necessarily
  wrong; the world moved.

The log doubles as (a) a scoring input for ``score_maturation_value()``
(adoption_success_factor; gated by ``refinement.enabled``), and (b) an
in-the-wild approximation of the 0.8.3 κ agreement gate by correlating
kept-maturation verdicts with downstream user-confirmed outcomes.

The writes happen unconditionally — even when ``refinement.enabled``
is False — so the log accumulates from day one. Only the *scoring
consumer* is behind the flag.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

AdoptionOutcomeState = Literal["pending", "confirmed", "rejected", "stale"]


def new_adoption_outcome_id() -> str:
    """Fresh uuid4-based id for a :class:`PredictionAdoptionOutcome`."""
    return uuid.uuid4().hex


def prediction_label_hash(label: str, anchor: str) -> str:
    """Stable identity hash for a prediction across cycles / sessions.

    The in-memory ``PredictionSpec.id`` is a per-spec digest that can
    change if a spec's source or hypothesis shape changes. For the
    adoption log we want a *label-level* identity so "the same
    question asked across cycles" aggregates in one bucket. Keys the
    ``adoption_success_factor`` lookup in WS4 scoring.
    """

    return hashlib.sha1(f"{label}|{anchor}".encode()).hexdigest()[:16]


@dataclass(slots=True)
class PredictionAdoptionOutcome:
    """One row in the adoption-outcome log.

    Invariants:
    - ``outcome == 'pending'`` ⇒ ``resolved_at is None``
    - ``outcome != 'pending'`` ⇒ ``resolved_at`` is the cycle-end epoch
      when the sweep (or rollback hook) flipped the state.
    - ``rollback_reason`` is only populated when ``outcome == 'rejected'``.
    - ``had_kept_maturation`` is snapshotted at adoption time (``bool``
      stored as ``int`` in SQLite — 0 / 1). Needed for the
      ``judge_field_accuracy`` bench metric.
    """

    id: str
    prediction_id: str
    prediction_label_hash: str
    adopted_at: float
    revision_at_adoption: int
    had_kept_maturation: bool
    workspace_root: str
    source: str
    outcome: AdoptionOutcomeState = "pending"
    resolved_at: float | None = None
    rollback_reason: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def new_pending(
        cls,
        *,
        prediction_id: str,
        label: str,
        anchor: str,
        revision_at_adoption: int,
        workspace_root: str,
        source: str,
        metadata: dict[str, str] | None = None,
    ) -> PredictionAdoptionOutcome:
        """Build a pending outcome row at adoption time."""

        return cls(
            id=new_adoption_outcome_id(),
            prediction_id=prediction_id,
            prediction_label_hash=prediction_label_hash(label, anchor),
            adopted_at=time.time(),
            revision_at_adoption=int(revision_at_adoption),
            had_kept_maturation=revision_at_adoption > 0,
            workspace_root=workspace_root,
            source=source,
            outcome="pending",
            metadata=dict(metadata or {}),
        )


__all__ = [
    "AdoptionOutcomeState",
    "PredictionAdoptionOutcome",
    "new_adoption_outcome_id",
    "prediction_label_hash",
]
