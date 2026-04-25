# SPDX-License-Identifier: Apache-2.0
"""`<VANER_ADOPTED_PACKAGE version="1" expires_at="...">` formatter.

Emits the full Resolution package a user has adopted, for direct injection
into the next LLM turn. The model is expected to answer from this block and
NOT re-call `vaner.resolve` or `vaner.predictions.active` for the same
intent while the block is fresh.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from vaner.integrations.injection.tokens import count_tokens, truncate_to_budget

TokenCounter = Callable[[str], int]

ADOPTED_VERSION = "1"


@dataclass(frozen=True)
class AdoptedPackagePayload:
    """Subset of a Resolution suitable for direct prompt injection.

    We intentionally keep this flat (no nested Pydantic model) so the
    formatter can be called from both the MCP server and the daemon HTTP
    handler without pulling the full Resolution type into the injection
    layer's import graph.
    """

    intent: str
    prepared_briefing: str | None
    predicted_response: str | None
    evidence_lines: list[str]
    provenance_summary: str
    adopted_from_prediction_id: str | None = None
    resolution_id: str | None = None


def build_adopted_package(
    payload: AdoptedPackagePayload,
    *,
    expires_at: datetime,
    budget_tokens: int,
    tokenizer: TokenCounter | None = None,
    include_provenance: bool = True,
    include_confidence_details: bool = False,
) -> str:
    """Render the adopted-package block. Returns empty if budget is zero."""
    if budget_tokens <= 0:
        return ""
    _ = include_confidence_details  # kept for parity with digest signature

    # Build the block with generous sections, then trim longest free-form
    # fields (briefing, draft) to fit the budget. Keeps structural markers
    # intact even under extreme truncation.
    expires_iso = expires_at.replace(microsecond=0).isoformat()
    open_tag = f'<VANER_ADOPTED_PACKAGE version="{ADOPTED_VERSION}" expires_at="{expires_iso}">'
    close_tag = "</VANER_ADOPTED_PACKAGE>"

    parts: list[str] = [open_tag]
    parts.append("Intent:")
    parts.append(payload.intent.strip())
    parts.append("")

    if payload.prepared_briefing and payload.prepared_briefing.strip():
        parts.append("Prepared briefing:")
        parts.append(payload.prepared_briefing.strip())
        parts.append("")

    if payload.predicted_response and payload.predicted_response.strip():
        parts.append("Predicted response:")
        parts.append(payload.predicted_response.strip())
        parts.append("")

    if payload.evidence_lines:
        parts.append("Evidence:")
        for line in payload.evidence_lines:
            parts.append(f"- {line}")
        parts.append("")

    if include_provenance and payload.provenance_summary.strip():
        parts.append("Provenance:")
        parts.append(payload.provenance_summary.strip())
        if payload.adopted_from_prediction_id:
            parts.append(f"Adopted from prediction: {payload.adopted_from_prediction_id}")
        if payload.resolution_id:
            parts.append(f"Resolution id: {payload.resolution_id}")
        parts.append("")

    parts.append(close_tag)
    text = "\n".join(parts).rstrip() + "\n"

    if count_tokens(text, tokenizer=tokenizer) <= budget_tokens:
        return text

    # Budget exceeded — truncate the longest two fields first
    # (prepared_briefing + predicted_response), then fall back to a hard
    # truncator on the full block as a safety net.
    return _truncate_with_structure(
        payload,
        expires_at=expires_at,
        budget_tokens=budget_tokens,
        tokenizer=tokenizer,
        include_provenance=include_provenance,
    )


def _truncate_with_structure(
    payload: AdoptedPackagePayload,
    *,
    expires_at: datetime,
    budget_tokens: int,
    tokenizer: TokenCounter | None,
    include_provenance: bool,
) -> str:
    # Give briefing + response 60% / 25% of budget; reserve remainder for
    # intent + evidence + provenance + tags (~15%).
    briefing_budget = max(1, int(budget_tokens * 0.60))
    response_budget = max(1, int(budget_tokens * 0.25))
    trimmed_briefing = (
        truncate_to_budget(
            payload.prepared_briefing,
            budget_tokens=briefing_budget,
            tokenizer=tokenizer,
        )
        if payload.prepared_briefing
        else None
    )
    trimmed_response = (
        truncate_to_budget(
            payload.predicted_response,
            budget_tokens=response_budget,
            tokenizer=tokenizer,
        )
        if payload.predicted_response
        else None
    )
    reduced = AdoptedPackagePayload(
        intent=payload.intent,
        prepared_briefing=trimmed_briefing,
        predicted_response=trimmed_response,
        evidence_lines=payload.evidence_lines[:3],
        provenance_summary=payload.provenance_summary,
        adopted_from_prediction_id=payload.adopted_from_prediction_id,
        resolution_id=payload.resolution_id,
    )
    rendered = build_adopted_package(
        reduced,
        expires_at=expires_at,
        budget_tokens=budget_tokens + 999_999,  # disable recursion
        tokenizer=tokenizer,
        include_provenance=include_provenance,
    )
    # Final safety net — if even the reduced form overshoots, hard-trim.
    if count_tokens(rendered, tokenizer=tokenizer) <= budget_tokens:
        return rendered
    return truncate_to_budget(rendered, budget_tokens=budget_tokens, tokenizer=tokenizer)
