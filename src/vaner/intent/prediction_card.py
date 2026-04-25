# SPDX-License-Identifier: Apache-2.0
"""Server-side derivation of UI card fields from a PredictedPrompt.

The MCP `vaner.predictions.dashboard` tool and the daemon HTTP
`/predictions/active` endpoint both need the same derived fields
(readiness_label, eta_bucket, adoptable, rank, ui_summary,
suppression_reason, source_label). Keeping the derivation here, rather
than duplicating in each surface, means the Rust / Swift / TS contract
mirrors only need to ship the wire types — each client reads the same
server-derived values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vaner.intent.prediction import PredictedPrompt
from vaner.intent.readiness import (
    EtaBucket,
    eta_bucket,
    eta_bucket_label,
    is_adoptable,
    readiness_label,
    suppression_reason,
)

_SOURCE_LABELS: dict[str, str] = {
    "arc": "Recent work",
    "pattern": "Conversation pattern",
    "llm_branch": "Model signal",
    "macro": "Workspace signal",
    "history": "Historical pattern",
    "goal": "Active goal",
}


@dataclass(frozen=True)
class CardDerivations:
    """Flat derivation result — JSON-serializable via :meth:`as_dict`."""

    readiness_label: str
    eta_bucket: EtaBucket | None
    eta_bucket_label: str | None
    adoptable: bool
    suppression_reason: str | None
    source_label: str
    ui_summary: str
    scenarios_complete: int
    scenarios_spawned: int
    tokens_used: int
    token_budget: int
    has_briefing: bool
    has_draft: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "readiness_label": self.readiness_label,
            "eta_bucket": self.eta_bucket,
            "eta_bucket_label": self.eta_bucket_label,
            "adoptable": self.adoptable,
            "suppression_reason": self.suppression_reason,
            "source_label": self.source_label,
            "ui_summary": self.ui_summary,
            "scenarios_complete": self.scenarios_complete,
            "scenarios_spawned": self.scenarios_spawned,
            "tokens_used": self.tokens_used,
            "token_budget": self.token_budget,
            "has_briefing": self.has_briefing,
            "has_draft": self.has_draft,
        }


def derive_card_fields(prompt: PredictedPrompt) -> CardDerivations:
    """Compute the UI-facing card derivations for *prompt*."""
    bucket = eta_bucket(prompt)
    return CardDerivations(
        readiness_label=readiness_label(prompt.run.readiness),
        eta_bucket=bucket,
        eta_bucket_label=eta_bucket_label(bucket),
        adoptable=is_adoptable(prompt),
        suppression_reason=suppression_reason(prompt),
        source_label=_source_label(prompt),
        ui_summary=_ui_summary(prompt),
        scenarios_complete=prompt.run.scenarios_complete,
        scenarios_spawned=prompt.run.scenarios_spawned,
        tokens_used=prompt.run.tokens_used,
        token_budget=prompt.run.token_budget,
        has_briefing=bool(prompt.artifacts.prepared_briefing and prompt.artifacts.prepared_briefing.strip()),
        has_draft=bool(prompt.artifacts.draft_answer and prompt.artifacts.draft_answer.strip()),
    )


def rank_cards(prompts: list[PredictedPrompt]) -> list[PredictedPrompt]:
    """Order predictions for the dashboard: adoptable-first, ready > drafting.

    Preserves the incoming order for everything below the adoptable bar so
    the underlying ranker (which sorts by confidence × evidence × goal
    alignment) stays visible.
    """
    readiness_priority = {
        "ready": 0,
        "drafting": 1,
        "evidence_gathering": 2,
        "grounding": 3,
        "queued": 4,
        "stale": 5,
    }
    return sorted(
        prompts,
        key=lambda p: (
            0 if is_adoptable(p) else 1,
            readiness_priority.get(p.run.readiness, 9),
            -p.spec.confidence,
        ),
    )


def _source_label(prompt: PredictedPrompt) -> str:
    return _SOURCE_LABELS.get(prompt.spec.source, "Recent work")


def _ui_summary(prompt: PredictedPrompt) -> str:
    desc = prompt.spec.description or ""
    label = prompt.spec.label
    # Short summary — the UI card title is the label; summary is one sentence
    # of supplementary context. Trim to ~140 chars to keep cards compact.
    if not desc or desc.strip() == label.strip():
        return label
    summary = desc.strip()
    if len(summary) > 140:
        summary = summary[:137].rstrip() + "…"
    return summary
