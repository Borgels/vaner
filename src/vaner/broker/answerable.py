# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
from pathlib import Path

from vaner.broker.selector import _prompt_terms
from vaner.models.answerable import (
    Answerability,
    AnswerabilityMetadata,
    AnswerableBriefing,
    AnswerableEvidenceItem,
    AnswerableEvidenceSection,
    ConflictNote,
    EvidenceAssemblyDecision,
    EvidenceAssemblyMetadata,
    EvidenceAssemblyMode,
    EvidenceChannel,
    EvidenceDecision,
    EvidenceRole,
)
from vaner.models.artefact import Artefact
from vaner.policy.budget import count_tokens

_SECTION_BUDGETS = {
    "direct_answer_evidence": 0.62,
    "supporting_context": 0.28,
    "lower_confidence_context": 0.10,
}


def build_answerable_briefing(
    query: str,
    artefacts: list[Artefact],
    *,
    repo_root: Path | None = None,
    max_tokens: int = 1200,
    channels_by_key: dict[str, str] | None = None,
    conflict_notes: list[str] | None = None,
    assembly_mode: EvidenceAssemblyMode = "shadow",
    quality_bias: str = "protect_recall",
    cost_sensitivity: str = "balanced",
) -> AnswerableBriefing:
    terms = _prompt_terms(query)
    scored = [
        _build_item(query, terms, artefact, repo_root=repo_root, channel=_channel_for(artefact, channels_by_key)) for artefact in artefacts
    ]
    scored.sort(key=lambda row: (row[0], -row[1], row[2].path), reverse=True)

    direct: list[AnswerableEvidenceItem] = []
    supporting: list[AnswerableEvidenceItem] = []
    lower: list[AnswerableEvidenceItem] = []
    omitted: list[str] = []
    for score, _position, item in scored:
        if score >= 12.0:
            direct.append(item)
        elif score >= 5.0:
            supporting.append(item)
        elif score > 0.0:
            lower.append(item)
        else:
            omitted.append(item.path)

    assembly_plan = _build_assembly_plan(
        direct=direct,
        supporting=supporting,
        lower=lower,
        mode=assembly_mode,
        quality_bias=quality_bias,
        cost_sensitivity=cost_sensitivity,
        baseline_tokens=0,
        result_tokens=0,
        conflict_paths=[],
    )

    if assembly_mode == "safe":
        direct, supporting, lower = _apply_safe_assembly(direct, supporting, lower, assembly_plan)

    if assembly_mode == "safe":
        (
            direct,
            supporting,
            lower,
            dropped_direct,
            dropped_supporting,
            dropped_lower,
            direct_truncated,
            supporting_truncated,
            lower_truncated,
        ) = _cap_by_transport_priority(
            direct,
            supporting,
            lower,
            max_tokens,
        )
    else:
        direct, dropped_direct, direct_truncated = _cap_items(
            direct, int(max_tokens * _SECTION_BUDGETS["direct_answer_evidence"]), min_items=1
        )
        supporting, _dropped_supporting, supporting_truncated = _cap_items(
            supporting,
            int(max_tokens * _SECTION_BUDGETS["supporting_context"]),
            min_items=0,
        )
        lower, _dropped_lower, lower_truncated = _cap_items(
            lower, int(max_tokens * _SECTION_BUDGETS["lower_confidence_context"]), min_items=0
        )
        dropped_supporting = _dropped_supporting
        dropped_lower = _dropped_lower

    conflicts = [
        ConflictNote(reason=note, paths=[item.path for item in direct[:3]], channels=_channels_present(direct + supporting + lower))
        for note in (conflict_notes or [])
        if note.strip()
    ]
    answerability, reason = _classify_answerability(direct, supporting, lower, conflicts, direct_truncated, dropped_direct)
    answer_plan = _answer_plan(query, direct, supporting, lower, conflicts)
    sections = [
        AnswerableEvidenceSection(kind="direct_answer_evidence", items=direct),
        AnswerableEvidenceSection(kind="supporting_context", items=supporting),
        AnswerableEvidenceSection(kind="lower_confidence_context", items=lower),
    ]
    if conflicts:
        sections.append(
            AnswerableEvidenceSection(
                kind="conflicts_and_cautions",
                items=[
                    AnswerableEvidenceItem(
                        path="",
                        title="Conflict note",
                        source="vaner",
                        why_selected=conflict.reason,
                        excerpt=conflict.reason,
                        relevance_to_query="conflicting evidence channels",
                        confidence=0.0,
                        token_count=count_tokens(conflict.reason),
                    )
                    for conflict in conflicts
                ],
            )
        )
    sections.append(
        AnswerableEvidenceSection(
            kind="provenance",
            items=[
                AnswerableEvidenceItem(
                    path=item.path,
                    title=item.title,
                    source=item.source,
                    channel=item.channel,
                    why_selected=item.why_selected,
                    excerpt=f"{item.channel}: {item.path}",
                    relevance_to_query=item.relevance_to_query,
                    confidence=item.confidence,
                    token_count=count_tokens(item.path),
                    revision_or_hash=item.revision_or_hash,
                )
                for item in (direct + supporting + lower)[:10]
            ],
        )
    )
    text = _render(query, answer_plan, sections)
    used = count_tokens(text)
    assembly_plan = assembly_plan.model_copy(
        update={
            "baseline_context_tokens": used - sum(decision.token_delta for decision in assembly_plan.decisions if decision.token_delta < 0),
            "result_context_tokens": used,
            "token_delta": sum(decision.token_delta for decision in assembly_plan.decisions if decision.token_delta < 0),
        }
    )
    transport_limited_count = dropped_direct + dropped_supporting + dropped_lower
    if transport_limited_count:
        assembly_plan = _mark_transport_limited(assembly_plan, direct + supporting + lower, transport_limited_count)
    truncation_applied = direct_truncated or supporting_truncated or lower_truncated or dropped_direct > 0
    metadata = AnswerabilityMetadata(
        answerability=answerability,
        answerability_reason=reason,
        direct_evidence_count=len(direct),
        supporting_evidence_count=len(supporting),
        lower_confidence_evidence_count=len(lower),
        conflict_count=len(conflicts),
        primary_paths=[item.path for item in direct[:3]],
        omitted_relevant_paths=omitted[:10],
        token_budget_used=used,
        truncation_applied=truncation_applied,
        truncation_risk="high" if direct_truncated or dropped_direct else ("medium" if supporting_truncated or lower_truncated else "low"),
        direct_evidence_truncated=direct_truncated,
        critical_excerpt_preserved=bool(direct) and not direct_truncated,
        dropped_direct_evidence_count=dropped_direct,
        evidence_channels_present=_channels_present(direct + supporting + lower),
        evidence_assembly=assembly_plan,
    )
    return AnswerableBriefing(
        text=text,
        answer_plan=answer_plan,
        sections=sections,
        metadata=metadata,
        conflicts=conflicts,
    )


def build_answerable_briefing_from_text(
    query: str,
    briefing_text: str,
    *,
    channel: EvidenceChannel = "prediction",
    max_tokens: int = 1200,
    assembly_mode: EvidenceAssemblyMode = "shadow",
) -> AnswerableBriefing:
    excerpt, truncated = _best_excerpt(briefing_text, _prompt_terms(query), max_chars=max_tokens * 4)
    token_count = count_tokens(excerpt)
    item = AnswerableEvidenceItem(
        path="prepared_briefing",
        title="Prepared briefing",
        source="prediction",
        channel=channel,
        why_selected="pre-existing prepared briefing",
        excerpt=excerpt,
        relevance_to_query="prepared evidence for matched prediction",
        confidence=0.7 if excerpt.strip() else 0.0,
        token_count=token_count,
        truncated=truncated,
    )
    answerability = "weak" if excerpt.strip() else "none"
    reason = "Prepared briefing exists but may require synthesis." if excerpt.strip() else "No prepared evidence is available."
    answer_plan = (
        "Use the prepared briefing as the primary evidence; do not add unsupported facts."
        if excerpt.strip()
        else "No strong evidence is available; abstain or state what is missing."
    )
    sections = [
        AnswerableEvidenceSection(
            kind="direct_answer_evidence" if excerpt.strip() else "lower_confidence_context", items=[item] if excerpt.strip() else []
        ),
        AnswerableEvidenceSection(kind="supporting_context", items=[]),
        AnswerableEvidenceSection(kind="lower_confidence_context", items=[]),
        AnswerableEvidenceSection(kind="provenance", items=[item] if excerpt.strip() else []),
    ]
    text = _render(query, answer_plan, sections)
    text_tokens = count_tokens(text)
    if assembly_mode == "off":
        assembly = EvidenceAssemblyMetadata(assembly_mode="off", baseline_context_tokens=text_tokens, result_context_tokens=text_tokens)
    else:
        assembly = EvidenceAssemblyMetadata(
            assembly_mode=assembly_mode,
            baseline_context_tokens=text_tokens,
            result_context_tokens=text_tokens,
            items_included=1 if excerpt.strip() else 0,
            items_protected=1 if excerpt.strip() else 0,
            decision_reasons=["prepared briefing preserved"] if excerpt.strip() else [],
            protected_reasons=["prepared prediction evidence"] if excerpt.strip() else [],
            decisions=[
                EvidenceAssemblyDecision(
                    path="prepared_briefing",
                    role="direct_answer_evidence",
                    decision="include_protected",
                    reason="prepared prediction briefing is protected in v1 assembly",
                    protected_reason="prepared prediction evidence",
                    directness_score=item.confidence,
                    source_reliability=0.8,
                )
            ]
            if excerpt.strip()
            else [],
        )
    metadata = AnswerabilityMetadata(
        answerability=answerability,  # type: ignore[arg-type]
        answerability_reason=reason,
        direct_evidence_count=1 if excerpt.strip() else 0,
        primary_paths=["prepared_briefing"] if excerpt.strip() else [],
        token_budget_used=text_tokens,
        truncation_applied=truncated,
        truncation_risk="medium" if truncated else "low",
        direct_evidence_truncated=truncated,
        critical_excerpt_preserved=not truncated and bool(excerpt.strip()),
        evidence_channels_present=[channel] if excerpt.strip() else [],
        evidence_assembly=assembly,
    )
    return AnswerableBriefing(text=text, answer_plan=answer_plan, sections=sections, metadata=metadata)


def _build_item(
    query: str,
    terms: list[str],
    artefact: Artefact,
    *,
    repo_root: Path | None,
    channel: EvidenceChannel,
) -> tuple[float, int, AnswerableEvidenceItem]:
    source_text = _source_text(artefact, repo_root)
    excerpt, truncated = _best_excerpt(source_text or artefact.content, terms)
    path = artefact.source_path
    score = _score_path_and_excerpt(query, path.lower(), excerpt.lower(), terms)
    title = _title_for(path, excerpt)
    confidence = max(0.0, min(1.0, score / 24.0))
    return (
        score,
        -len(path),
        AnswerableEvidenceItem(
            path=path,
            title=title,
            source=str(artefact.metadata.get("corpus_id", "default")),
            channel=channel,
            why_selected=_why_selected(path, terms, score),
            excerpt=excerpt,
            relevance_to_query=_relevance(query, path, terms, score),
            confidence=confidence,
            token_count=count_tokens(excerpt),
            revision_or_hash=str(artefact.metadata.get("revision") or artefact.metadata.get("hash") or "") or None,
            truncated=truncated,
        ),
    )


def _build_assembly_plan(
    *,
    direct: list[AnswerableEvidenceItem],
    supporting: list[AnswerableEvidenceItem],
    lower: list[AnswerableEvidenceItem],
    mode: EvidenceAssemblyMode,
    quality_bias: str,
    cost_sensitivity: str,
    baseline_tokens: int,
    result_tokens: int,
    conflict_paths: list[str],
) -> EvidenceAssemblyMetadata:
    if mode == "off":
        return EvidenceAssemblyMetadata(
            assembly_mode="off",
            quality_bias=quality_bias,
            cost_sensitivity=cost_sensitivity,
            baseline_context_tokens=baseline_tokens,
            result_context_tokens=result_tokens,
            token_delta=result_tokens - baseline_tokens,
        )
    decisions: list[EvidenceAssemblyDecision] = []
    seen: dict[str, AnswerableEvidenceItem] = {}
    conflict_path_set = set(conflict_paths)
    for role, items in (
        ("direct_answer_evidence", direct),
        ("supporting_context", supporting),
        ("lower_confidence_context", lower),
    ):
        for item in items:
            duplicate_of = _duplicate_of(item, seen)
            protected_reason = _protected_reason(item, role, conflict_path_set)
            if duplicate_of:
                decision: EvidenceDecision = "dedupe" if mode in {"shadow", "advisory"} else "omit_duplicate"
                reason = "duplicate excerpt or source path"
            elif protected_reason:
                decision = "include_protected"
                reason = "protected evidence retained"
            elif role == "lower_confidence_context" and mode in {"shadow", "advisory", "safe"}:
                decision = "demote"
                reason = "lower-confidence context should stay behind direct evidence"
            else:
                decision = "include"
                reason = "evidence retained"
            evidence_role: EvidenceRole = "duplicate_context" if duplicate_of else role  # type: ignore[assignment]
            decisions.append(
                EvidenceAssemblyDecision(
                    path=item.path,
                    role=evidence_role,
                    decision=decision,
                    reason=reason,
                    protected_reason=protected_reason,
                    token_delta=-item.token_count if decision == "omit_duplicate" else 0,
                    duplicate_of=duplicate_of,
                    conflict_related=item.path in conflict_path_set,
                    directness_score=item.confidence,
                    source_reliability=_source_reliability(item),
                    would_change_output_in_shadow=bool(duplicate_of),
                )
            )
            if duplicate_of is None:
                seen[_dedupe_key(item)] = item
    protected_reasons = sorted({decision.protected_reason for decision in decisions if decision.protected_reason})
    reasons = sorted({decision.reason for decision in decisions if decision.reason})
    return EvidenceAssemblyMetadata(
        assembly_mode=mode,
        quality_bias=quality_bias,
        cost_sensitivity=cost_sensitivity,
        baseline_context_tokens=baseline_tokens,
        result_context_tokens=result_tokens,
        items_included=sum(1 for decision in decisions if decision.decision in {"include", "include_protected", "demote", "dedupe"}),
        items_protected=sum(1 for decision in decisions if decision.decision == "include_protected"),
        items_deduped=sum(1 for decision in decisions if decision.decision in {"dedupe", "omit_duplicate"}),
        items_demoted=sum(1 for decision in decisions if decision.decision == "demote"),
        items_omitted_duplicate=sum(1 for decision in decisions if decision.decision == "omit_duplicate"),
        decision_reasons=reasons,
        protected_reasons=protected_reasons,
        token_delta=result_tokens - baseline_tokens,
        decisions=decisions,
    )


def _apply_safe_assembly(
    direct: list[AnswerableEvidenceItem],
    supporting: list[AnswerableEvidenceItem],
    lower: list[AnswerableEvidenceItem],
    assembly: EvidenceAssemblyMetadata,
) -> tuple[list[AnswerableEvidenceItem], list[AnswerableEvidenceItem], list[AnswerableEvidenceItem]]:
    if not any(decision.decision == "omit_duplicate" for decision in assembly.decisions):
        return direct, supporting, lower
    seen: set[str] = set()

    def keep_unique(items: list[AnswerableEvidenceItem]) -> list[AnswerableEvidenceItem]:
        kept: list[AnswerableEvidenceItem] = []
        for item in items:
            key = _dedupe_key(item)
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
        return kept

    return (keep_unique(direct), keep_unique(supporting), keep_unique(lower))


def _mark_transport_limited(
    assembly: EvidenceAssemblyMetadata,
    kept_items: list[AnswerableEvidenceItem],
    transport_limited_count: int,
) -> EvidenceAssemblyMetadata:
    kept_paths = {item.path for item in kept_items}
    updated: list[EvidenceAssemblyDecision] = []
    marked = 0
    for decision in assembly.decisions:
        if decision.path not in kept_paths and decision.decision not in {"omit_duplicate", "transport_limited"}:
            updated.append(
                decision.model_copy(
                    update={
                        "decision": "transport_limited",
                        "reason": "evidence was constrained by the caller transport limit",
                        "would_change_output_in_shadow": True,
                    }
                )
            )
            marked += 1
        else:
            updated.append(decision)
    return assembly.model_copy(
        update={
            "items_transport_limited": assembly.items_transport_limited + max(transport_limited_count, marked),
            "technical_limit_hit": "answerable_briefing_transport_limit",
            "decision_reasons": sorted(set(assembly.decision_reasons + ["evidence was constrained by the caller transport limit"])),
            "decisions": updated,
        }
    )


def _duplicate_of(item: AnswerableEvidenceItem, seen: dict[str, AnswerableEvidenceItem]) -> str | None:
    key = _dedupe_key(item)
    if key in seen:
        return seen[key].path
    return None


def _dedupe_key(item: AnswerableEvidenceItem) -> str:
    excerpt = re.sub(r"\s+", " ", item.excerpt.strip().lower())
    return f"{item.path.lower()}::{excerpt[:240]}"


def _protected_reason(item: AnswerableEvidenceItem, role: str, conflict_paths: set[str]) -> str:
    if role == "direct_answer_evidence":
        return "direct answer evidence"
    if item.path in conflict_paths:
        return "conflict evidence"
    if item.channel == "prediction":
        return "prepared prediction evidence"
    return ""


def _source_reliability(item: AnswerableEvidenceItem) -> float:
    path = item.path.lower()
    score = 0.5
    if path.startswith("docs/") or "/docs/" in path:
        score += 0.2
    if item.channel == "prediction":
        score += 0.1
    if item.revision_or_hash:
        score += 0.1
    return min(1.0, score)


def _source_text(artefact: Artefact, repo_root: Path | None) -> str:
    if repo_root is None or not artefact.source_path:
        return ""
    path = repo_root / artefact.source_path
    try:
        if path.is_file() and path.stat().st_size <= 400_000:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def _best_excerpt(text: str, terms: list[str], *, max_chars: int = 950) -> tuple[str, bool]:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned, False
    lines = cleaned.splitlines()
    best_index = 0
    best_score = -1
    term_set = [term for term in terms if len(term) > 3]
    for index, line in enumerate(lines):
        lowered = line.lower()
        score = sum(1 for term in term_set if term in lowered)
        if line.lstrip().startswith("#"):
            score += 1
        if score > best_score:
            best_index = index
            best_score = score
    start = max(0, best_index - 3)
    excerpt_lines: list[str] = []
    total = 0
    for line in lines[start:]:
        if total + len(line) + 1 > max_chars:
            break
        excerpt_lines.append(line)
        total += len(line) + 1
    excerpt = "\n".join(excerpt_lines).strip() or cleaned[:max_chars].strip()
    return excerpt, True


def _score_path_and_excerpt(query: str, path: str, excerpt: str, terms: list[str]) -> float:
    score = 0.0
    term_score = 0.0
    for term in terms:
        if len(term) < 4:
            continue
        if term in path:
            term_score += 4.0
        if term in excerpt:
            term_score += 2.0
    score += term_score
    q = query.lower()
    if term_score > 0.0 and _english_prompt_likely(query):
        if path.startswith("docs/en/") or "/en/docs/" in path:
            score += 8.0
        elif re.search(r"(^|/)docs/[a-z]{2}(-[a-z]{2})?/", path):
            score -= 8.0
    if (
        term_score > 0.0
        and any(term in q for term in ("official", "documentation", "docs"))
        and (path.startswith("docs/") or "/docs/" in path)
    ):
        score += 4.0
    if any(term in q for term in ("security", "auth", "oauth", "token", "scheme")):
        if "/security/" in path:
            score += 10.0
        if any(term in excerpt for term in ("security", "authentication", "oauth", "token", "bearer")):
            score += 4.0
    if "first" in terms and "steps" in terms and "first-steps" in path:
        score += 10.0
    if any(term in q for term in ("dependency", "dependencies", "depends")):
        if "/dependencies/" in path:
            score += 10.0
        if "dependency injection" in excerpt or "depends" in excerpt:
            score += 4.0
    return score


def _cap_items(items: list[AnswerableEvidenceItem], token_budget: int, *, min_items: int) -> tuple[list[AnswerableEvidenceItem], int, bool]:
    kept: list[AnswerableEvidenceItem] = []
    used = 0
    truncated = False
    for item in items:
        if kept and used + item.token_count > token_budget and len(kept) >= min_items:
            continue
        if used + item.token_count > token_budget:
            trimmed = _trim_to_tokens(item.excerpt, max(80, token_budget - used))
            item = item.model_copy(update={"excerpt": trimmed, "token_count": count_tokens(trimmed), "truncated": True})
            truncated = True
        kept.append(item)
        used += item.token_count
        if used >= token_budget and len(kept) >= min_items:
            break
    return kept, max(0, len(items) - len(kept)), truncated


def _cap_by_transport_priority(
    direct: list[AnswerableEvidenceItem],
    supporting: list[AnswerableEvidenceItem],
    lower: list[AnswerableEvidenceItem],
    token_limit: int,
) -> tuple[
    list[AnswerableEvidenceItem],
    list[AnswerableEvidenceItem],
    list[AnswerableEvidenceItem],
    int,
    int,
    int,
    bool,
    bool,
    bool,
]:
    kept_direct: list[AnswerableEvidenceItem] = []
    kept_supporting: list[AnswerableEvidenceItem] = []
    kept_lower: list[AnswerableEvidenceItem] = []
    used = 0
    direct_truncated = False
    supporting_truncated = False
    lower_truncated = False
    dropped_direct = 0
    dropped_supporting = 0
    dropped_lower = 0

    def keep_group(
        items: list[AnswerableEvidenceItem],
        target: list[AnswerableEvidenceItem],
        *,
        role: str,
        protected: bool,
    ) -> bool:
        nonlocal used, direct_truncated, supporting_truncated, lower_truncated, dropped_direct, dropped_supporting, dropped_lower
        truncated_any = False
        for index, item in enumerate(items):
            if used + item.token_count <= token_limit:
                target.append(item)
                used += item.token_count
                continue
            if protected and not target:
                trimmed = _trim_to_tokens(item.excerpt, max(80, token_limit - used))
                target.append(item.model_copy(update={"excerpt": trimmed, "token_count": count_tokens(trimmed), "truncated": True}))
                used += target[-1].token_count
                truncated_any = True
                continue
            if protected:
                dropped_direct += len(items) - index
            elif role == "supporting_context":
                dropped_supporting += len(items) - index
            else:
                dropped_lower += len(items) - index
            break
        return truncated_any

    direct_truncated = keep_group(direct, kept_direct, role="direct_answer_evidence", protected=True)
    supporting_truncated = keep_group(supporting, kept_supporting, role="supporting_context", protected=False)
    lower_truncated = keep_group(lower, kept_lower, role="lower_confidence_context", protected=False)
    return (
        kept_direct,
        kept_supporting,
        kept_lower,
        dropped_direct,
        dropped_supporting,
        dropped_lower,
        direct_truncated,
        supporting_truncated,
        lower_truncated,
    )


def _trim_to_tokens(text: str, budget: int) -> str:
    max_chars = max(120, budget * 4)
    return text[:max_chars].rstrip()


def _classify_answerability(
    direct: list[AnswerableEvidenceItem],
    supporting: list[AnswerableEvidenceItem],
    lower: list[AnswerableEvidenceItem],
    conflicts: list[ConflictNote],
    direct_truncated: bool,
    dropped_direct: int,
) -> tuple[Answerability, str]:
    if conflicts:
        return "conflict", "Relevant evidence exists, but evidence channels disagree."
    if direct and not direct_truncated and dropped_direct == 0:
        return "full", "Direct evidence is ranked first and preserved within the briefing budget."
    if direct or supporting:
        return "weak", "Relevant evidence exists, but it is incomplete, broad, or partially truncated."
    if lower:
        return "weak", "Only lower-confidence evidence matched the query."
    return "none", "No relevant evidence was strong enough to support an answer."


def _answer_plan(
    query: str,
    direct: list[AnswerableEvidenceItem],
    supporting: list[AnswerableEvidenceItem],
    lower: list[AnswerableEvidenceItem],
    conflicts: list[ConflictNote],
) -> str:
    if conflicts and direct:
        return (
            f"Prefer direct evidence from {direct[0].path} for factual claims; "
            "handle conflict notes explicitly and do not silently merge channels."
        )
    if direct:
        plan = f"Use {direct[0].path} as the primary source for the answer."
        if len(direct) > 1:
            plan += f" Cross-check with {direct[1].path} only for supporting details."
        if lower:
            plan += " Treat lower-confidence context as fallback only."
        return plan
    if supporting:
        return f"Use {supporting[0].path} cautiously; answer only claims directly supported by the excerpt."
    return "No strong evidence is available; abstain or state what is missing."


def _render(query: str, answer_plan: str, sections: list[AnswerableEvidenceSection]) -> str:
    parts = [
        "## Query",
        query.strip(),
        "",
        "## answer_plan",
        answer_plan.strip(),
        "",
    ]
    for section in sections:
        parts.append(f"## {section.kind}")
        if not section.items:
            parts.append("(none)")
            parts.append("")
            continue
        for index, item in enumerate(section.items, start=1):
            label = item.path or item.title or section.kind
            parts.append(f"### {index}. {label}")
            if item.channel:
                parts.append(f"- channel: {item.channel}")
            if item.why_selected:
                parts.append(f"- why_selected: {item.why_selected}")
            if item.relevance_to_query:
                parts.append(f"- relevance: {item.relevance_to_query}")
            parts.append(item.excerpt.strip() or "(no excerpt)")
            parts.append("")
    return "\n".join(parts).strip()


def _channel_for(artefact: Artefact, channels_by_key: dict[str, str] | None) -> EvidenceChannel:
    raw = (channels_by_key or {}).get(artefact.key) or str(artefact.metadata.get("provenance", "vaner_resolve"))
    if raw in {"prediction", "vaner_resolve", "retrieval_floor", "external_rag"}:
        return raw  # type: ignore[return-value]
    return "vaner_resolve"


def _channels_present(items: list[AnswerableEvidenceItem]) -> list[EvidenceChannel]:
    return sorted({item.channel for item in items})


def _english_prompt_likely(prompt: str) -> bool:
    ascii_chars = sum(1 for char in prompt if ord(char) < 128)
    return ascii_chars / max(1, len(prompt)) > 0.95 and "translate" not in prompt.lower()


def _title_for(path: str, excerpt: str) -> str:
    for line in excerpt.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.rsplit("/", 1)[-1] if path else ""


def _why_selected(path: str, terms: list[str], score: float) -> str:
    matched = [term for term in terms if len(term) > 3 and term in path.lower()][:5]
    reason = f"relevance_score={score:.1f}"
    if matched:
        reason += "; path matched " + ", ".join(matched)
    return reason


def _relevance(query: str, path: str, terms: list[str], score: float) -> str:
    if score >= 12.0:
        return "directly answers or anchors the query"
    if score >= 5.0:
        return "supports the query but may need synthesis"
    return "lower-confidence contextual match"
