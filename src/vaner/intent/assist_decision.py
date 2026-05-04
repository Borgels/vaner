# SPDX-License-Identifier: Apache-2.0
"""Turn-start relevance and assist-decision policy for Vaner.

This module is intentionally transport-agnostic. MCP, HTTP, injected prompt
digests, and UIs should consume these fields instead of independently
inferring adoption behavior from raw prediction confidence.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

AssistAction = Literal["use_adopted_package", "adopt_prediction", "resolve_optional", "answer_normally"]
MatchState = Literal["strong_match", "weak_match", "unrelated", "stale"]
RecommendedAction = Literal["adopt", "inspect", "ignore"]
SnapshotFreshness = Literal["ready", "warming", "stale", "cold"]


@dataclass(frozen=True)
class AssistDecisionPolicy:
    """Tunable thresholds for conservative turn adoption.

    The defaults prefer a lower adoption rate over wrong adoption. Callers and
    tests can override the policy without changing the product contract.
    """

    min_query_tokens_for_resolve: int = 4
    min_overlap_tokens: int = 2
    prompt_jaccard_threshold: float = 0.22
    strong_signal_count: int = 2
    label_max_chars: int = 96
    summary_max_chars: int = 160


@dataclass(frozen=True)
class PredictionRelevance:
    display_label: str
    match_state: MatchState
    match_reason: str
    recommended_action: RecommendedAction
    snapshot_freshness: SnapshotFreshness
    relevance_signals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VanerAssistDecision:
    action: AssistAction
    reason: str
    prediction_id: str | None = None
    package_id: str | None = None
    query: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


_TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]+")
_INTERNAL_LABEL_PREFIX_RE = re.compile(r"^(goal|step|item|task|raw|category)\s*:\s*", re.IGNORECASE)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "we",
    "what",
    "when",
    "where",
    "why",
    "with",
    "you",
}


def evaluate_prediction_relevance(
    row: dict[str, Any],
    query: str = "",
    *,
    context: dict[str, Any] | None = None,
    policy: AssistDecisionPolicy | None = None,
) -> PredictionRelevance:
    """Return conservative, query-aware relevance for a prediction row."""

    policy = policy or AssistDecisionPolicy()
    context = context or {}
    label = normalize_prediction_label(row, policy=policy)
    summary = _safe_text(
        _row_get(row, "ui_summary") or _row_get(row, "description") or _row_get(row, "spec.description") or label,
        fallback=label,
        max_len=policy.summary_max_chars,
    )
    readiness = str(_row_get(row, "readiness") or _row_get(row, "run.readiness") or "").lower()
    freshness = str(_row_get(row, "freshness") or "").lower()
    trust_status = str(_row_get(row, "trust_status") or "").lower()
    diagnostic_status = str(_row_get(row, "diagnostic_status") or "").lower()
    snapshot_freshness = _snapshot_freshness(readiness, freshness, trust_status)

    if readiness == "stale" or freshness == "stale" or trust_status == "invalidated" or diagnostic_status == "invalidated":
        return PredictionRelevance(
            display_label=label,
            match_state="stale",
            match_reason="Prepared context is stale or invalidated.",
            recommended_action="ignore",
            snapshot_freshness="stale",
            relevance_signals=[],
        )

    has_material = _has_ready_material(row, readiness)
    signals = _relevance_signals(row, query, label=label, summary=summary, context=context, policy=policy)
    if has_material and _is_strong_adoption_match(row, label=label, signals=signals, policy=policy):
        return PredictionRelevance(
            display_label=label,
            match_state="strong_match",
            match_reason=f"Ready prepared context matches the current turn via {', '.join(signals[:3])}.",
            recommended_action="adopt",
            snapshot_freshness=snapshot_freshness,
            relevance_signals=signals,
        )
    if has_material and signals:
        return PredictionRelevance(
            display_label=label,
            match_state="weak_match",
            match_reason=f"Prepared context has one relevance signal ({signals[0]}), but not enough for automatic adoption.",
            recommended_action="inspect",
            snapshot_freshness=snapshot_freshness,
            relevance_signals=signals,
        )
    if readiness in {"ready", "drafting"} and not has_material:
        reason = "Prediction is ready or drafting but lacks a prepared briefing or draft payload."
    elif query.strip():
        reason = "No strong current-turn overlap with this prepared context."
    else:
        reason = "No current-turn query was supplied for relevance matching."
    return PredictionRelevance(
        display_label=label,
        match_state="unrelated",
        match_reason=reason,
        recommended_action="ignore",
        snapshot_freshness=snapshot_freshness,
        relevance_signals=signals,
    )


def build_assist_decision(
    query: str,
    predictions: list[dict[str, Any]],
    *,
    context: dict[str, Any] | None = None,
    has_retrieval_candidate: bool = False,
    engine_unavailable: bool = False,
    policy: AssistDecisionPolicy | None = None,
) -> VanerAssistDecision:
    """Decide what Vaner should contribute at the start of a primary-AI turn."""

    policy = policy or AssistDecisionPolicy()
    context = context or {}
    package_id = _fresh_adopted_package_id(context)
    if package_id:
        return VanerAssistDecision(
            action="use_adopted_package",
            package_id=package_id,
            reason="A fresh adopted Vaner package is already present for this turn.",
        )

    strong = [
        row
        for row in predictions
        if str(row.get("recommended_action") or "") == "adopt" and str(row.get("match_state") or "") == "strong_match"
    ]
    if strong:
        first = strong[0]
        return VanerAssistDecision(
            action="adopt_prediction",
            prediction_id=str(first.get("id") or first.get("prediction_id") or ""),
            reason=str(first.get("match_reason") or "One prepared context package strongly matches this user turn."),
        )

    query_tokens = tokenize(query)
    if not engine_unavailable and has_retrieval_candidate and len(query_tokens) >= policy.min_query_tokens_for_resolve:
        return VanerAssistDecision(
            action="resolve_optional",
            query=query,
            reason=(
                "No prepared context is strong enough to adopt, but stored evidence may help; "
                "the primary AI should continue normally if resolve is unavailable or slow."
            ),
        )

    reason = "No clearly relevant prepared context is ready for this turn."
    if engine_unavailable:
        reason = "Vaner engine is unavailable, so the primary AI should answer normally."
    return VanerAssistDecision(action="answer_normally", reason=reason)


def normalize_prediction_label(row: dict[str, Any], *, policy: AssistDecisionPolicy | None = None) -> str:
    """Return a human-readable normal-surface label for a prediction row."""

    policy = policy or AssistDecisionPolicy()
    source_label = str(_row_get(row, "source_label") or _row_get(row, "spec.source") or _row_get(row, "source") or "prepared context")
    raw = _safe_text(
        _row_get(row, "display_label") or _row_get(row, "label") or _row_get(row, "spec.label") or _row_get(row, "title"),
        fallback="",
        max_len=policy.label_max_chars,
    )
    cleaned = _strip_markup(raw)
    if _looks_like_generic_path_label(cleaned):
        return _fallback_label(row, source_label=source_label, policy=policy)
    internal = _INTERNAL_LABEL_PREFIX_RE.match(cleaned)
    if internal:
        cleaned = cleaned[internal.end() :].strip()
        if _looks_like_bad_label(cleaned):
            return _fallback_label(row, source_label=source_label, policy=policy)
        topic = cleaned[0].lower() + cleaned[1:] if cleaned else "the current task"
        return _safe_text(
            f"Prepare context for {topic}",
            fallback=_fallback_label(row, source_label=source_label, policy=policy),
            max_len=policy.label_max_chars,
        )
    if _looks_like_bad_label(cleaned):
        return _fallback_label(row, source_label=source_label, policy=policy)
    return _safe_text(cleaned, fallback=_fallback_label(row, source_label=source_label, policy=policy), max_len=policy.label_max_chars)


def tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(text.lower()):
        for part in re.split(r"[/_.-]+", raw):
            if len(part) >= 2 and part not in _STOPWORDS:
                tokens.add(part)
    return tokens


def _relevance_signals(
    row: dict[str, Any],
    query: str,
    *,
    label: str,
    summary: str,
    context: dict[str, Any],
    policy: AssistDecisionPolicy,
) -> list[str]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    signals: list[str] = []
    text_tokens = tokenize(f"{label} {summary}")
    overlap = query_tokens & text_tokens
    union = query_tokens | text_tokens
    if len(overlap) >= policy.min_overlap_tokens or (union and len(overlap) / len(union) >= policy.prompt_jaccard_threshold):
        signals.append("prompt overlap")

    path_text = " ".join(str(item) for item in list(_row_get(row, "watched_sources") or []) + list(_row_get(row, "changed_sources") or []))
    path_overlap = query_tokens & tokenize(path_text)
    if path_overlap:
        signals.append("workspace/source overlap")

    structured = _row_get(row, "structured") or _row_get(row, "spec.structured") or {}
    if isinstance(structured, dict):
        structured_overlap = query_tokens & tokenize(" ".join(str(value) for value in structured.values() if value is not None))
        if len(structured_overlap) >= policy.min_overlap_tokens:
            signals.append("task/object overlap")

    active_plan_text = _context_text(context, ("active_plan", "plan", "plan_title", "plan_summary", "task", "current_task"))
    if active_plan_text:
        plan_overlap = query_tokens & tokenize(active_plan_text)
        if len(plan_overlap) >= policy.min_overlap_tokens:
            signals.append("active plan overlap")

    source = str(_row_get(row, "source") or _row_get(row, "spec.source") or "")
    if source == "composer_intent":
        signals.append("fresh draft signal")

    return signals


def _is_strong_adoption_match(
    row: dict[str, Any],
    *,
    label: str,
    signals: list[str],
    policy: AssistDecisionPolicy,
) -> bool:
    if len(signals) < policy.strong_signal_count:
        return False
    if _is_broad_prediction(row, label=label):
        return False
    if "fresh draft signal" in signals and "prompt overlap" in signals:
        return True
    if "prompt overlap" not in signals:
        return False
    return "workspace/source overlap" in signals or "task/object overlap" in signals


def _is_broad_prediction(row: dict[str, Any], *, label: str) -> bool:
    source = str(_row_get(row, "source") or _row_get(row, "spec.source") or "").lower()
    specificity = str(_row_get(row, "specificity") or _row_get(row, "spec.specificity") or "").lower()
    if source in {"artefact_item", "goal", "horizon", "macro"} and specificity != "concrete":
        return True
    return _looks_like_generic_path_label(label)


def _has_ready_material(row: dict[str, Any], readiness: str) -> bool:
    if readiness not in {"ready", "drafting"}:
        return False
    if bool(_row_get(row, "has_draft") or _row_get(row, "artifacts.has_draft")):
        return True
    if bool(_row_get(row, "has_briefing") or _row_get(row, "artifacts.has_briefing")):
        return True
    return bool(_row_get(row, "artifacts.draft_answer") or _row_get(row, "artifacts.prepared_briefing"))


def _snapshot_freshness(readiness: str, freshness: str, trust_status: str) -> SnapshotFreshness:
    if readiness == "stale" or freshness == "stale" or trust_status == "invalidated":
        return "stale"
    if readiness in {"ready", "drafting"} and trust_status in {"ready", ""}:
        return "ready"
    if readiness:
        return "warming"
    return "cold"


def _fresh_adopted_package_id(context: dict[str, Any]) -> str | None:
    for key in ("adopted_package_id", "package_id", "vaner_adopted_package_id"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    package = context.get("adopted_package") or context.get("vaner_adopted_package")
    if isinstance(package, dict):
        value = package.get("id") or package.get("resolution_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _fallback_label(row: dict[str, Any], *, source_label: str, policy: AssistDecisionPolicy) -> str:
    summary = _strip_internal_summary_prefix(
        _strip_markup(str(_row_get(row, "ui_summary") or _row_get(row, "description") or _row_get(row, "spec.description") or ""))
    )
    if summary and not _looks_like_bad_label(summary):
        return _safe_text(summary, fallback="Prepared context for the current task", max_len=policy.label_max_chars)
    path_label = _path_area_label(_row_get(row, "watched_sources") or _row_get(row, "changed_sources") or [])
    if path_label:
        return _safe_text(
            f"Prepare context for {path_label}",
            fallback="Prepared context for the current task",
            max_len=policy.label_max_chars,
        )
    clean_source = source_label.strip().lower()
    if clean_source and clean_source != "prediction":
        return _safe_text(
            f"Prepared context from {clean_source}",
            fallback="Prepared context for the current task",
            max_len=policy.label_max_chars,
        )
    return "Prepared context for the current task"


def _path_area_label(paths: Any) -> str | None:
    if not isinstance(paths, list) or not paths:
        return None
    first = str(paths[0]).strip()
    if not first:
        return None
    parts = [part for part in re.split(r"[/\\]+", first) if part and part not in {".", ".."}]
    if not parts:
        return None
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def _looks_like_generic_path_label(text: str) -> bool:
    stripped = _strip_markup(text).lower()
    match = re.fullmatch(r"prepare context for ([a-z0-9_.-]+/[a-z0-9_.-]+)", stripped)
    if not match:
        return False
    first, second = match.group(1).split("/", 1)
    return first in {"src", "ui", "tests", "plugins", "cursor-plugins"} and second in {"vaner", "cockpit"}


def _strip_internal_summary_prefix(text: str) -> str:
    return re.sub(r"^Artefact item under goal '[^']+':\s*", "", text).strip()


def _looks_like_bad_label(text: str) -> bool:
    original = text.strip()
    stripped = original.strip(" .:-_`")
    if not stripped:
        return True
    tokens = tokenize(stripped)
    if len(tokens) <= 1 and len(stripped) <= 4:
        return True
    if len(original) >= 56 and not original.endswith((".", "?", "!", ")", "]", "`")):
        return True
    return False


def _strip_markup(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.replace("`", "").strip())
    cleaned = cleaned.replace("\\n", " ").strip()
    return cleaned


def _safe_text(value: Any, *, fallback: str, max_len: int) -> str:
    text = _strip_markup(str(value or ""))
    if not text:
        return fallback
    if len(text) <= max_len:
        return text
    trimmed = text[: max(1, max_len - 1)].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return f"{trimmed}..."


def _row_get(row: dict[str, Any], key: str) -> Any:
    current: Any = row
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _context_text(context: dict[str, Any], keys: tuple[str, ...]) -> str:
    parts: list[str] = []
    for key in keys:
        value = context.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value.values() if isinstance(item, str))
        elif isinstance(value, list):
            parts.extend(str(item) for item in value if isinstance(item, str))
    return " ".join(parts)
