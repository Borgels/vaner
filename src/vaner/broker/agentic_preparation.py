# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Iterable, Mapping

from vaner.broker.context_preparation import infer_context_preparation_profile
from vaner.models.agentic_preparation import AgenticRecallPlan, EvidenceCandidate, EvidenceSupportSelection
from vaner.models.context_preparation import ContextPreparationProfile

_MULTI_SOURCE_MARKERS = (
    " across ",
    " all ",
    " compare",
    " each ",
    " every ",
    " list ",
    " which documents",
    " which sources",
    " conflict",
    " conflicting",
    " summarize",
    " themes",
    " patterns",
    " how many",
    " count ",
    " counts ",
    " by team",
    " by owner",
    " by dri",
    " end-to-end",
)


def support_item_budget(
    prompt: str,
    *,
    profile: ContextPreparationProfile | None = None,
    default_max: int = 12,
) -> int:
    """Return a minimal direct-support budget for a context request.

    Single-fact requests should usually preserve one direct source. Broad
    synthesis/comparison requests need a larger support set. The decision is
    derived from general task language and the inferred context need.
    """

    profile = profile or infer_context_preparation_profile(prompt)
    if profile.need in {"multi_source_synthesis", "conflict_resolution", "research_mapping"}:
        return max(1, default_max)
    text = f" {prompt.strip().lower()} "
    if any(marker in text for marker in _MULTI_SOURCE_MARKERS):
        return max(1, default_max)
    if " and " in text and any(token in text for token in ("what ", "which ", "who ", "when ", "where ", "why ", "how ")):
        return max(1, min(default_max, 3))
    return 1


def excerpt_text(text: str, *, max_chars: int = 900) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "..."


def candidate_rows(candidates: Iterable[EvidenceCandidate], *, max_excerpt_chars: int = 900) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, candidate in enumerate(candidates, start=1):
        rows.append(
            {
                "rank": candidate.rank or index,
                "key": candidate.key,
                "path": candidate.path,
                "title": candidate.title,
                "excerpt": excerpt_text(candidate.text, max_chars=max_excerpt_chars),
            }
        )
    return rows


def build_recall_planning_prompt(
    prompt: str,
    candidates: Iterable[EvidenceCandidate],
    *,
    max_queries: int = 6,
    max_seed_candidates: int = 24,
) -> str:
    rows = candidate_rows(list(candidates)[:max_seed_candidates], max_excerpt_chars=500)
    return (
        "You are Vaner's bounded context preparation agent. Your job is to improve evidence recall before a final "
        "user-facing answer or action model runs.\n"
        "Generate search queries that could find direct supporting context for the user's request. Use general "
        "work-context reasoning: exact entities, aliases, likely source vocabulary, code/config names, dates, owners, "
        "incidents, meetings, and paraphrases. Do not use benchmark IDs, gold answers, hidden labels, or dataset "
        "shortcuts.\n"
        "Return JSON only. Keep queries short and lexical enough for search.\n\n"
        f"User request: {prompt}\n\n"
        f"Current candidate summaries:\n{rows}\n\n"
        f"Return at most {max_queries} queries."
    )


def build_evidence_selection_prompt(
    prompt: str,
    candidates: Iterable[EvidenceCandidate],
    *,
    support_budget: int,
    max_candidates: int = 96,
    max_excerpt_chars: int = 900,
) -> str:
    rows = candidate_rows(list(candidates)[:max_candidates], max_excerpt_chars=max_excerpt_chars)
    return (
        "You are Vaner's evidence preparation agent. Select the smallest set of candidate items that directly support "
        "the user's request. Prefer exact evidence over topical similarity. Include multiple items only when the "
        "request asks for comparison, completeness, conflicts, synthesis, counts, or multiple facts. Do not include "
        "corroborating, background, nearby, follow-up, or merely topical items. For a one-fact request, select exactly "
        "one item only if it directly contains the answer. Return no items if the candidates do not contain the answer.\n"
        "This is a general context-preparation step, not a benchmark shortcut. Do not infer hidden expected sources; "
        "use only the request and candidate content.\n\n"
        f"User request: {prompt}\n\n"
        f"Candidate items:\n{rows}\n\n"
        f"Return at most {support_budget} support keys ordered by support strength."
    )


def coerce_recall_plan(payload: Mapping[str, object] | None, *, max_queries: int = 6, original_prompt: str = "") -> AgenticRecallPlan:
    raw_queries = payload.get("queries") if payload else None
    queries: list[str] = []
    if isinstance(raw_queries, list):
        for value in raw_queries:
            query = str(value).strip()
            if query and query.lower() != original_prompt.lower() and query not in queries:
                queries.append(query)
            if len(queries) >= max_queries:
                break
    reason = str(payload.get("reason", "")) if payload else ""
    return AgenticRecallPlan(queries=queries, reason=reason)


def coerce_support_selection(
    payload: Mapping[str, object] | None,
    *,
    allowed_keys: set[str],
    support_budget: int,
) -> EvidenceSupportSelection:
    raw_keys = None
    if payload:
        raw_keys = payload.get("support_keys")
        if raw_keys is None:
            raw_keys = payload.get("document_ids")
    support_keys: list[str] = []
    if isinstance(raw_keys, list):
        for value in raw_keys:
            key = str(value).strip()
            if key in allowed_keys and key not in support_keys:
                support_keys.append(key)
            if len(support_keys) >= support_budget:
                break
    answerability = str(payload.get("answerability", "none")) if payload else "none"
    if answerability not in {"full", "partial", "none"}:
        answerability = "none"
    coverage_note = str(payload.get("coverage_note", "")) if payload else ""
    return EvidenceSupportSelection(
        support_keys=support_keys,
        coverage_note=coverage_note,
        answerability=answerability,  # type: ignore[arg-type]
    )


def promote_support_candidates(
    candidates: Iterable[EvidenceCandidate],
    support_keys: Iterable[str],
) -> list[EvidenceCandidate]:
    support_order = list(dict.fromkeys(str(key) for key in support_keys))
    by_key = {candidate.key: candidate for candidate in candidates}
    promoted = [by_key[key] for key in support_order if key in by_key]
    promoted.extend(candidate for candidate in candidates if candidate.key not in set(support_order))
    return promoted
