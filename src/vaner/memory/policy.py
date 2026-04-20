# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from vaner.mcp.contracts import ConflictSignal, FeedbackRating, MemoryState

ReuseVerdict = Literal["reuse_payload", "rerank_prior", "ignore_prior"]

PROMOTION_CONFIDENCE_MIN = 0.75
PROMOTION_EVIDENCE_MIN = 2
PROMOTION_CONTRADICTION_MAX = 0.20
PROMOTION_SUCCESS_STREAK_MIN = 2
CONFLICT_GAP_THRESHOLD = 0.35


class InvalidMemoryTransition(ValueError):
    pass


_ALLOWED_TRANSITIONS: dict[MemoryState, set[MemoryState]] = {
    "candidate": {"trusted", "stale", "demoted", "candidate"},
    "trusted": {"trusted", "stale", "demoted"},
    "stale": {"stale", "candidate"},
    "demoted": {"demoted", "candidate"},
}


def _validate_transition(current: MemoryState, target: MemoryState) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidMemoryTransition(f"Invalid transition: {current} -> {target}")


@dataclass(slots=True)
class PromotionContext:
    rating: FeedbackRating | None
    resolution_confidence: float
    evidence_count: int
    contradiction_signal: float
    prior_successes: int
    has_explicit_pin: bool
    correction_confirmed: bool


@dataclass(slots=True)
class PromotionDecision:
    from_state: MemoryState
    to_state: MemoryState
    reason: str


@dataclass(slots=True)
class InvalidationContext:
    fingerprints_at_validation: list[str]
    fingerprints_now: list[str]
    memory_confidence: float


@dataclass(slots=True)
class NegativeFeedbackContext:
    rating: FeedbackRating
    had_contradiction: bool
    prior_successes: int


@dataclass(slots=True)
class ConflictInput:
    compiled_sections: dict[str, str]
    compiled_entities: set[str]
    compiled_fingerprints: list[str]
    fresh_entities: set[str]
    fresh_fingerprints: list[str]


@dataclass(slots=True)
class ReuseInput:
    evidence_fresh: bool
    envelope_similarity: float
    contradiction_since_last_validation: bool
    memory_state: MemoryState


def decide_promotion(ctx: PromotionContext, current: MemoryState) -> PromotionDecision:
    target = current
    reason = "unchanged"
    if current == "candidate":
        if ctx.has_explicit_pin:
            target = "trusted"
            reason = "explicit_pin"
        elif ctx.prior_successes >= PROMOTION_SUCCESS_STREAK_MIN:
            target = "trusted"
            reason = "success_streak"
        elif (
            ctx.rating == "useful"
            and ctx.resolution_confidence >= PROMOTION_CONFIDENCE_MIN
            and ctx.evidence_count >= PROMOTION_EVIDENCE_MIN
            and ctx.contradiction_signal < PROMOTION_CONTRADICTION_MAX
        ):
            target = "trusted"
            reason = "high_confidence_multi_evidence"
        elif ctx.correction_confirmed:
            target = "trusted"
            reason = "correction_confirmed"
        elif ctx.rating == "partial":
            target = "candidate"
            reason = "partial_kept_candidate"
    _validate_transition(current, target)
    return PromotionDecision(from_state=current, to_state=target, reason=reason)


def decide_invalidation(ctx: InvalidationContext, current: MemoryState) -> PromotionDecision | None:
    prev = set(ctx.fingerprints_at_validation)
    now = set(ctx.fingerprints_now)
    changed = bool(prev and prev - now)
    if not changed:
        return None
    if current == "trusted":
        target = "stale"
        reason = "trusted_fingerprint_drift"
    elif current == "candidate":
        if ctx.memory_confidence >= 0.4:
            target = "stale"
            reason = "candidate_fingerprint_drift"
        else:
            target = "demoted"
            reason = "candidate_low_confidence_drift"
    else:
        target = current
        reason = "no_change"
    _validate_transition(current, target)
    return PromotionDecision(from_state=current, to_state=target, reason=reason)


def decide_on_negative_feedback(ctx: NegativeFeedbackContext, current: MemoryState) -> PromotionDecision:
    if ctx.rating == "wrong":
        target = "demoted"
        reason = "wrong_feedback_demote"
    elif ctx.rating == "irrelevant" and current == "trusted":
        target = "stale"
        reason = "irrelevant_trusted_to_stale"
    else:
        target = current
        reason = "irrelevant_soft_no_demote"
    _validate_transition(current, target)
    return PromotionDecision(from_state=current, to_state=target, reason=reason)


def detect_conflict(inp: ConflictInput) -> ConflictSignal:
    compiled_fp = set(inp.compiled_fingerprints)
    fresh_fp = set(inp.fresh_fingerprints)
    fp_diff_ratio = 0.0
    if compiled_fp:
        fp_diff_ratio = len(compiled_fp - fresh_fp) / max(1, len(compiled_fp))

    union = inp.compiled_entities | inp.fresh_entities
    entity_ratio = 0.0
    if union:
        entity_ratio = len(inp.compiled_entities ^ inp.fresh_entities) / len(union)

    compiled_text = " ".join(inp.compiled_sections.values()).lower()
    fresh_text = " ".join(sorted(inp.fresh_entities)).lower()
    keyword_conflict = 0.0
    if re.search(r"\bmiddleware\b", compiled_text) and re.search(r"\broute\b", fresh_text):
        keyword_conflict = 0.6
    if re.search(r"\broute\b", compiled_text) and re.search(r"\bmiddleware\b", fresh_text):
        keyword_conflict = max(keyword_conflict, 0.6)

    strength = max(fp_diff_ratio, entity_ratio, keyword_conflict)
    return ConflictSignal(
        has_conflict=strength >= CONFLICT_GAP_THRESHOLD,
        strength=round(min(1.0, max(0.0, strength)), 4),
        reason="fingerprint/entity/claim mismatch" if strength >= CONFLICT_GAP_THRESHOLD else "",
    )


def decide_reuse(inp: ReuseInput) -> ReuseVerdict:
    if (
        inp.memory_state == "trusted"
        and inp.evidence_fresh
        and inp.envelope_similarity >= 0.75
        and not inp.contradiction_since_last_validation
    ):
        return "reuse_payload"
    if inp.memory_state in {"trusted", "candidate"} and inp.evidence_fresh and inp.envelope_similarity >= 0.5:
        return "rerank_prior"
    return "ignore_prior"


def evidence_fingerprint(source_path: str, locator: dict, content_hash: str | None, weight: float) -> str:
    normalized = {
        "source_path": source_path.strip(),
        "locator": locator,
        "content_hash": content_hash or "",
        "weight_bucket": round(float(weight), 2),
    }
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode("utf-8")).hexdigest()
    return digest
