# SPDX-License-Identifier: Apache-2.0
"""Structured Prediction Engine v2 primitives.

v2 predicts ranked next actions plus evidence targets. It deliberately
separates evidence readiness from draft readiness: evidence can be prepared
aggressively, while draft adoption requires semantic compatibility.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from vaner.intent.target_normalization import component_terms, normalize_component

ActionType = Literal[
    "unknown",
    "explain",
    "implement",
    "debug",
    "test",
    "review",
    "summarize",
    "compare",
    "plan",
    "commit",
    "inspect",
]
ReadinessMode = Literal["evidence_ready", "draft_ready"]
SymbolRelation = Literal[
    "definition_match",
    "test_match",
    "usage_match",
    "doc_match",
    "generated_match",
    "broad_content_match",
]

_ACTION_TERMS: dict[ActionType, tuple[str, ...]] = {
    "explain": ("explain", "understand", "why", "how"),
    "implement": ("implement", "add", "build", "create", "write", "make"),
    "debug": ("debug", "fix", "bug", "error", "traceback", "exception", "failing", "failed"),
    "test": ("test", "tests", "coverage", "assert", "spec"),
    "review": ("review", "audit", "critique", "feedback"),
    "summarize": ("summarize", "summary", "recap"),
    "compare": ("compare", "versus", "vs", "tradeoff", "tradeoffs"),
    "plan": ("plan", "design", "architecture", "roadmap", "strategy"),
    "commit": ("commit", "stage", "git"),
    "inspect": ("inspect", "find", "read", "investigate", "search", "show"),
}

_STOPWORDS = {
    "about",
    "after",
    "again",
    "and",
    "can",
    "for",
    "from",
    "how",
    "into",
    "need",
    "please",
    "that",
    "the",
    "this",
    "with",
    "you",
}


@dataclass(frozen=True, slots=True)
class StructuredPrediction:
    action_type: ActionType
    object: str
    answer_shape: str
    evidence_targets: tuple[str, ...] = field(default_factory=tuple)
    semantic_hint: str = ""
    readiness_mode: ReadinessMode = "evidence_ready"
    confidence: float = 0.0
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    abstain_reason: str = ""
    evidence_fresh_at: float = field(default_factory=time.time)
    contradicted_by: tuple[str, ...] = field(default_factory=tuple)
    readiness_reason: str = ""


@dataclass(frozen=True, slots=True)
class EvidenceTarget:
    path: str
    score: float
    signals: tuple[str, ...] = field(default_factory=tuple)
    role: str = "supporting"
    role_reason: str = ""
    symbol_score: float = 0.0
    path_score: float = 0.0
    content_score: float = 0.0
    graph_score: float = 0.0
    recency_score: float = 0.0
    role_score: float = 0.0
    symbol_relation: SymbolRelation | str = ""
    wrong_primary_risk: float = 0.0
    wrong_primary_reasons: tuple[str, ...] = field(default_factory=tuple)
    readiness_reason: str = ""


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    compatible: bool
    score: float
    action_score: float
    target_score: float
    answer_shape_score: float
    evidence_score: float
    freshness_score: float
    reason: str


def infer_action_type(text: str) -> ActionType:
    lowered = text.lower()
    words = [token for token in re.split(r"[^A-Za-z0-9_]+", lowered) if token]
    first = words[0] if words else ""
    if first in {"explain", "understand", "why", "how"}:
        return "explain"
    if any(term in lowered for term in ("test", "tests", "coverage", "assert", "spec")):
        return "test"
    best: tuple[int, ActionType] = (0, "unknown")
    for action, terms in _ACTION_TERMS.items():
        hits = sum(1 for term in terms if term in lowered)
        if hits > best[0]:
            best = (hits, action)
    return best[1]


def infer_answer_shape(text: str, action_type: ActionType) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ("plan", "proposal", "roadmap")):
        return "plan"
    if any(term in lowered for term in ("patch", "implement", "fix", "code", "test")) or action_type in {"implement", "debug", "test"}:
        return "code_or_patch"
    if any(term in lowered for term in ("compare", "versus", "vs", "tradeoff")) or action_type == "compare":
        return "comparison"
    if any(term in lowered for term in ("summary", "summarize", "recap")) or action_type == "summarize":
        return "summary"
    if action_type in {"inspect", "explain", "review"}:
        return "investigation_report"
    return "short_answer"


def normalize_target(value: str) -> str:
    cleaned = value.strip().replace("\\", "/")
    if not cleaned:
        return ""
    if cleaned.startswith("/"):
        return PurePosixPath(cleaned).name
    if ":" in cleaned and re.match(r"^[A-Za-z]:/", cleaned):
        return PurePosixPath(cleaned[2:]).name
    return cleaned


def structured_from_prediction_fields(
    *,
    label: str,
    description: str = "",
    anchor: str = "",
    evidence_targets: tuple[str, ...] = (),
    readiness_mode: ReadinessMode = "evidence_ready",
    confidence: float = 0.0,
    reason_codes: tuple[str, ...] = (),
) -> StructuredPrediction:
    text = " ".join(part for part in (label, description, anchor) if part)
    action_type = infer_action_type(text)
    target = normalize_target(anchor) or _object_from_text(text)
    answer_shape = infer_answer_shape(text, action_type)
    target_terms = component_terms(text)
    exact_evidence = _has_exact_evidence(target_terms, evidence_targets)
    final_readiness = readiness_mode
    readiness_reason = "requested"
    if readiness_mode == "draft_ready" and target_terms and not exact_evidence and not _is_docs_contract_shaped(text):
        final_readiness = "evidence_ready"
        readiness_reason = "weak_component_match"
    elif readiness_mode == "draft_ready" and target_terms:
        readiness_reason = "exact_component_evidence"
    return StructuredPrediction(
        action_type=action_type,
        object=target,
        answer_shape=answer_shape,
        evidence_targets=tuple(normalize_target(item) for item in evidence_targets if normalize_target(item)),
        semantic_hint=text,
        readiness_mode=final_readiness,
        confidence=max(0.0, min(1.0, float(confidence))),
        reason_codes=reason_codes,
        abstain_reason="weak_component_match" if readiness_reason == "weak_component_match" else "",
        readiness_reason=readiness_reason,
    )


def compatibility_for_query(
    query: str,
    structured: StructuredPrediction,
    *,
    invalidation_reason: str = "",
    now: float | None = None,
    max_evidence_age_seconds: float = 900.0,
) -> CompatibilityResult:
    query_action = infer_action_type(query)
    action_score = 1.0 if query_action == structured.action_type else 0.0
    if query_action == "unknown" or structured.action_type == "unknown":
        action_score = 0.35

    query_shape = infer_answer_shape(query, query_action)
    answer_shape_score = 1.0 if query_shape == structured.answer_shape else 0.35

    q_tokens = _meaningful_tokens(query)
    target_tokens = _meaningful_tokens(" ".join((structured.object, structured.semantic_hint, *structured.evidence_targets)))
    target_score = _jaccard(q_tokens, target_tokens)
    if not target_tokens:
        target_score = 0.35
    elif len(q_tokens & target_tokens) >= min(2, len(q_tokens)):
        target_score = max(target_score, 0.85)
    elif any(target in query for target in structured.evidence_targets if target):
        target_score = max(target_score, 0.85)

    evidence_score = 1.0 if structured.evidence_targets else 0.35
    age = max(0.0, (now or time.time()) - structured.evidence_fresh_at)
    freshness_score = 1.0 if age <= max_evidence_age_seconds else 0.0
    if structured.contradicted_by or invalidation_reason:
        freshness_score = 0.0

    score = 0.30 * action_score + 0.25 * target_score + 0.20 * answer_shape_score + 0.15 * evidence_score + 0.10 * freshness_score

    if structured.readiness_mode == "draft_ready":
        action_compatible = action_score >= 0.9 or (
            (query_action == "unknown" or structured.action_type == "unknown") and target_score >= 0.8
        )
        compatible = (
            score >= 0.72
            and action_compatible
            and target_score >= 0.40
            and answer_shape_score >= 0.9
            and freshness_score >= 0.9
            and evidence_score >= 0.9
        )
    else:
        generic_source = bool(set(structured.reason_codes) & {"goal", "horizon", "pattern", "seed_prior", "macro"})
        threshold = 0.62 if generic_source else 0.52
        semantic_alignment = action_score >= 0.9 or answer_shape_score >= 0.9 or (
            not generic_source and target_score >= 0.80
        )
        compatible = (
            score >= threshold
            and freshness_score >= 0.9
            and evidence_score >= 0.9
            and target_score >= 0.40
            and semantic_alignment
        )

    reason = "compatible" if compatible else ("related" if score >= 0.38 and freshness_score >= 0.9 else "incompatible")
    return CompatibilityResult(
        compatible=compatible,
        score=score,
        action_score=action_score,
        target_score=target_score,
        answer_shape_score=answer_shape_score,
        evidence_score=evidence_score,
        freshness_score=freshness_score,
        reason=reason,
    )


def _object_from_text(text: str) -> str:
    for token in text.split():
        cleaned = token.strip("`'\".,;:()[]{}")
        if "/" in cleaned or "." in cleaned:
            return normalize_target(cleaned)
    tokens = list(_meaningful_tokens(text))
    return " ".join(tokens[:4])


def _has_exact_evidence(target_terms: tuple[str, ...], evidence_targets: tuple[str, ...]) -> bool:
    if not target_terms or not evidence_targets:
        return False
    evidence_norms = {normalize_component(target) for target in evidence_targets}
    for target in evidence_targets:
        evidence_norms.update(component_terms(target))
    return bool(target_terms and target_terms[0] in evidence_norms)


def _is_docs_contract_shaped(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("doc", "docs", "schema", "contract", "api reference", "readme"))


def _meaningful_tokens(text: str) -> set[str]:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return {token for token in normalized.split() if len(token) > 2 and token not in _STOPWORDS}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))
