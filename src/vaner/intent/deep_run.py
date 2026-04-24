# SPDX-License-Identifier: Apache-2.0
"""WS1 — Overnight / Deep-Run Mode data model (0.8.3).

Deep-Run Mode is a *policy layer* on top of the engine. It distinguishes
"the user is incidentally idle" (a resource condition the engine already
tracks) from "the user has declared a long, predictable away window and
wants preparedness over immediacy" (an intent signal). When the user opts
in via CLI / MCP / cockpit / desktop, the engine adopts a different
stance for the duration of the window: longer per-cycle utilisation,
broader exploration frontier, deeper drafting bars, **maturation passes
on already-ready predictions**, and a persisted summary at the end.

This module owns the core types:

- :class:`DeepRunSession` — the persisted policy record. One row per
  declared window. The engine's governor consults the active session
  every cycle; the cockpit / desktop / CLI / MCP all read this single
  canonical record. Single-active-session is enforced at the store
  layer (see :mod:`vaner.store.deep_run`).
- :class:`DeepRunSummary` — post-session aggregate. Reports four
  honest counts (kept / discarded / rolled back / failed-3x) rather
  than a single inflated "matured" number, per the spec's anti-self-
  judging discipline (§9.2).
- :class:`DeepRunPassLogEntry` — one row in the per-pass audit log.
  Used by ``vaner.explain`` and the cockpit history panel; required
  for the maturation-effectiveness bench (§14).

The dataclass shape mirrors :mod:`vaner.intent.artefacts` so callers can
treat identity, mutable state, and audit records independently. There is
no dependency on ``goals.py``, ``prediction.py``, or ``governor.py`` —
status strings that cross module boundaries are typed as plain
``Literal`` here.

Cycle-safety note: the maturation-pass machinery (``MaturationContract``,
``MaturationPassRef``) lives in WS3 and is not declared here. WS1 ships
the session lifecycle + audit-log skeleton only.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

DeepRunPreset = Literal["conservative", "balanced", "aggressive"]
DeepRunFocus = Literal["active_goals", "current_workspace", "all_recent"]
DeepRunHorizonBias = Literal[
    "likely_next",
    "long_horizon",
    "finish_partials",
    "balanced",
]
DeepRunLocality = Literal["local_only", "local_preferred", "allow_cloud"]
DeepRunStatus = Literal["active", "paused", "ended", "killed"]

DeepRunPassAction = Literal[
    # Maturation lifecycle (WS3): kept = judge approved + persisted;
    # discarded = judge rejected; rolled_back = subsequent reconciliation
    # contradicted a kept maturation during its probation window;
    # failed = repeated revisits exhausted the per-prediction cap.
    "matured_kept",
    "matured_discarded",
    "matured_rolled_back",
    "matured_failed",
    # Non-maturation actions for completeness (the same audit log records
    # frontier exploration during a Deep-Run window so cycle composition
    # is reconstructable post-hoc).
    "promoted",
    "explored",
]

# Reasons a Deep-Run session may pause without ending. Surfaced in
# ``DeepRunSession.pause_reasons`` and in the user-facing status displays.
DeepRunPauseReason = Literal[
    "battery",
    "thermal",
    "user_input_observed",
    "engine_error_rate",
    "cost_cap_exceeded",
    "user_requested",
]


def new_deep_run_session_id() -> str:
    """Fresh uuid4-based id for a new :class:`DeepRunSession`."""
    return uuid.uuid4().hex


def new_deep_run_pass_id() -> str:
    """Fresh uuid4-based id for a :class:`DeepRunPassLogEntry`."""
    return uuid.uuid4().hex


@dataclass(slots=True)
class DeepRunSession:
    """The persisted policy record for one declared away window.

    Identity is the ``id`` (uuid). Lifecycle is captured by ``status``:
    a session moves from ``active`` to ``paused`` (and back) as resource
    or cost gates fire, and terminates at ``ended`` (clock-determined or
    user stop) or ``killed`` (immediate stop, in-flight cycle abandoned).

    Counters (``cycles_run``, ``matured_kept`` …) are updated by the
    engine at the end of each cycle. The four maturation-outcome
    counters together with ``cycles_run`` give a complete, honest
    accounting of what the session did — see spec §14.1 (the
    judge–external agreement gate would be impossible to interpret if
    the session reported only ``matured_kept``).

    ``spend_usd`` accumulates remote-backend cost (per
    ``ExplorationEndpoint.cost_per_1k_tokens``) and is the gate
    consulted by the router when ``cost_cap_usd > 0``.
    ``cost_cap_usd == 0`` is the safe default and means *no remote
    spend permitted* for the session — a hard router-layer block, not
    a budget warning.
    """

    id: str
    started_at: float
    ends_at: float
    preset: DeepRunPreset
    focus: DeepRunFocus
    horizon_bias: DeepRunHorizonBias
    locality: DeepRunLocality
    cost_cap_usd: float
    workspace_root: str
    status: DeepRunStatus
    pause_reasons: list[DeepRunPauseReason] = field(default_factory=list)
    spend_usd: float = 0.0
    cycles_run: int = 0
    matured_kept: int = 0
    matured_discarded: int = 0
    matured_rolled_back: int = 0
    matured_failed: int = 0
    promoted_count: int = 0
    ended_at: float | None = None
    cancelled_reason: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        ends_at: float,
        preset: DeepRunPreset,
        focus: DeepRunFocus,
        horizon_bias: DeepRunHorizonBias,
        locality: DeepRunLocality,
        cost_cap_usd: float,
        workspace_root: str,
        metadata: dict[str, str] | None = None,
        started_at: float | None = None,
    ) -> DeepRunSession:
        """Build a fresh active session with a new id and ``status="active"``."""

        return cls(
            id=new_deep_run_session_id(),
            started_at=started_at if started_at is not None else time.time(),
            ends_at=ends_at,
            preset=preset,
            focus=focus,
            horizon_bias=horizon_bias,
            locality=locality,
            cost_cap_usd=cost_cap_usd,
            workspace_root=workspace_root,
            status="active",
            metadata=dict(metadata or {}),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in ("ended", "killed")

    @property
    def matured_total(self) -> int:
        """Total maturation attempts across all four outcomes.

        Per the §9.2 honesty discipline, surfaces should always show all
        four sub-counters rather than this sum on its own. Provided here
        for arithmetic convenience (e.g. computing kept-rate in tests).
        """

        return self.matured_kept + self.matured_discarded + self.matured_rolled_back + self.matured_failed


@dataclass(slots=True)
class DeepRunPassLogEntry:
    """One row in the per-pass audit log.

    Records what happened to one prediction during one Deep-Run cycle.
    The engine writes one entry per maturation attempt (kept, discarded,
    rolled back, or failed) and also records ``promoted`` / ``explored``
    actions so cycle composition is fully reconstructable.

    ``contract_json`` and ``judge_verdict_json`` are populated by WS3
    (the maturation pass that constructs a ``MaturationContract`` and
    invokes the judge). For WS1, both default to ``None`` — only the
    skeleton schema lands now.
    """

    id: str
    session_id: str
    prediction_id: str
    pass_at: float
    action: DeepRunPassAction
    cycle_index: int
    before_evidence_score: float | None = None
    after_evidence_score: float | None = None
    before_draft_hash: str | None = None
    after_draft_hash: str | None = None
    contract_json: str | None = None
    judge_verdict_json: str | None = None

    @classmethod
    def new(
        cls,
        *,
        session_id: str,
        prediction_id: str,
        action: DeepRunPassAction,
        cycle_index: int,
        pass_at: float | None = None,
        before_evidence_score: float | None = None,
        after_evidence_score: float | None = None,
        before_draft_hash: str | None = None,
        after_draft_hash: str | None = None,
        contract_json: str | None = None,
        judge_verdict_json: str | None = None,
    ) -> DeepRunPassLogEntry:
        return cls(
            id=new_deep_run_pass_id(),
            session_id=session_id,
            prediction_id=prediction_id,
            pass_at=pass_at if pass_at is not None else time.time(),
            action=action,
            cycle_index=cycle_index,
            before_evidence_score=before_evidence_score,
            after_evidence_score=after_evidence_score,
            before_draft_hash=before_draft_hash,
            after_draft_hash=after_draft_hash,
            contract_json=contract_json,
            judge_verdict_json=judge_verdict_json,
        )


@dataclass(frozen=True, slots=True)
class DeepRunSummary:
    """Post-session aggregate.

    Built from a closed :class:`DeepRunSession` row plus the matching
    pass-log rows. Surfaced by ``vaner deep-run show <id>``,
    ``vaner.deep_run.show`` (MCP), the cockpit history detail drawer,
    and the desktop end-of-session notification.

    The four ``matured_*`` counts are reported separately, never
    collapsed into a single "matured" number — see spec §9.2 / §14.1.
    """

    session_id: str
    started_at: float
    ended_at: float
    preset: DeepRunPreset
    cycles_run: int
    matured_kept: int
    matured_discarded: int
    matured_rolled_back: int
    matured_failed: int
    promoted_count: int
    spend_usd: float
    pause_reasons: tuple[DeepRunPauseReason, ...]
    cancelled_reason: str | None
    final_status: DeepRunStatus

    @classmethod
    def from_session(cls, session: DeepRunSession) -> DeepRunSummary:
        if session.ended_at is None:
            raise ValueError("DeepRunSummary requires an ended session (ended_at is None — session is still active or paused)")
        return cls(
            session_id=session.id,
            started_at=session.started_at,
            ended_at=session.ended_at,
            preset=session.preset,
            cycles_run=session.cycles_run,
            matured_kept=session.matured_kept,
            matured_discarded=session.matured_discarded,
            matured_rolled_back=session.matured_rolled_back,
            matured_failed=session.matured_failed,
            promoted_count=session.promoted_count,
            spend_usd=session.spend_usd,
            pause_reasons=tuple(session.pause_reasons),
            cancelled_reason=session.cancelled_reason,
            final_status=session.status,
        )
