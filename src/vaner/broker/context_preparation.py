# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
from collections import Counter

from vaner.models.artefact import Artefact
from vaner.models.context_preparation import (
    ContextConstraint,
    ContextCoverageReport,
    ContextFacet,
    ContextPreparationProfile,
    ContextSourceStats,
    PreparedContextDiagnostics,
)

_PATH_RE = re.compile(r"\b(?:[\w.-]+/)+[\w.-]+\b")
_QUOTED_RE = re.compile(r"[`'\"]([^`'\"]{3,100})[`'\"]")
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
_RESTRICTIVE_RE = re.compile(
    r"\b(?:only|except|before|after|latest|current|superseded|newest|oldest|no later than|at least|at most)\b",
    re.IGNORECASE,
)


def infer_context_preparation_profile(prompt: str) -> ContextPreparationProfile:
    lowered = prompt.lower()
    archetype = _infer_archetype(lowered)
    need = _infer_need(lowered, archetype)
    constraints = _extract_constraints(prompt)
    facets = _extract_facets(prompt, constraints)
    source_hints = _extract_source_hints(lowered)
    expected_evidence_count = _expected_evidence_count(lowered, need)
    confidence = 0.55 + min(0.35, 0.05 * len(facets) + 0.06 * len(constraints))
    notes: list[str] = []
    if any(constraint.kind == "restrictive_language" for constraint in constraints):
        notes.append("hard_constraints_detected")
    if need in {"multi_source_synthesis", "conflict_resolution", "research_mapping"}:
        notes.append("coverage_sensitive")
    return ContextPreparationProfile(
        need=need,
        archetype=archetype,
        facets=facets,
        constraints=constraints,
        source_hints=source_hints,
        expected_evidence_count=expected_evidence_count,
        confidence=min(0.95, confidence),
        notes=notes,
    )


def query_variants(prompt: str, profile: ContextPreparationProfile, *, max_variants: int = 6) -> list[str]:
    variants = [prompt]
    facet_terms = [facet.value for facet in profile.facets if len(facet.value) > 2]
    constraint_terms = [constraint.value for constraint in profile.constraints if constraint.kind != "restrictive_language"]
    if facet_terms:
        variants.append(" ".join(facet_terms[:8]))
    if constraint_terms:
        variants.append(" ".join([*constraint_terms[:4], *facet_terms[:6]]))
    if profile.need in {"multi_source_synthesis", "research_mapping", "decision_support"}:
        variants.append(" ".join([*facet_terms[:10], "summary status decision evidence"]))
    if profile.need == "conflict_resolution":
        variants.append(" ".join([*facet_terms[:8], "current superseded conflict updated latest"]))
    if profile.need == "implementation_support":
        variants.append(" ".join([*facet_terms[:10], "implementation caller dependency test"]))
    if profile.need == "absence_check":
        variants.append(" ".join([*facet_terms[:8], "source truth reference policy"]))
    deduped = []
    for variant in variants:
        normalized = " ".join(variant.split())
        if normalized and normalized.lower() not in {item.lower() for item in deduped}:
            deduped.append(normalized)
        if len(deduped) >= max(1, max_variants):
            break
    return deduped


def exact_reference_candidates(prompt: str, available_paths: list[str], *, limit: int = 32) -> list[str]:
    references = set(_PATH_RE.findall(prompt))
    references.update(match.group(1).strip() for match in _QUOTED_RE.finditer(prompt))
    identifier_terms = {
        token.lower()
        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", prompt)
        if "_" in token or any(char.isupper() for char in token[1:])
    }
    ranked: list[tuple[int, str]] = []
    for path in available_paths:
        lowered_path = path.lower()
        score = 0
        for reference in references:
            ref = reference.strip().lower()
            if not ref:
                continue
            if ref == lowered_path or lowered_path.endswith(ref):
                score += 12
            elif ref in lowered_path:
                score += 6
        basename = lowered_path.rsplit("/", 1)[-1]
        stem = basename.rsplit(".", 1)[0]
        for term in identifier_terms:
            if term == stem or term in basename:
                score += 5
            elif term in lowered_path:
                score += 2
        if score:
            ranked.append((score, path))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [path for _, path in ranked[:limit]]


def hard_constraints_satisfied(prompt: str, artefact: Artefact, profile: ContextPreparationProfile) -> tuple[bool, list[str], list[str]]:
    text = f"{artefact.source_path}\n{artefact.content}".lower()
    satisfied: list[str] = []
    missing: list[str] = []
    for constraint in profile.constraints:
        value = constraint.value.lower()
        if constraint.kind == "restrictive_language":
            satisfied.append(constraint.value)
            continue
        if value in text:
            satisfied.append(constraint.value)
        elif constraint.required:
            missing.append(constraint.value)
    if _constraint_terms(prompt) and not satisfied and profile.need in {"direct_reference", "conflict_resolution", "absence_check"}:
        return False, satisfied, missing
    return not missing, satisfied, missing


def competitive_threshold_multiplier(need: str | None) -> float:
    if need in {"multi_source_synthesis", "conflict_resolution", "research_mapping", "decision_support"}:
        return 0.0
    if need in {"implementation_support", "absence_check", "working_set_extension", "creative_grounding"}:
        return 0.20
    if need == "evidence_gathering":
        return 0.30
    return 0.45


def build_coverage_report(
    profile: ContextPreparationProfile,
    selected: list[Artefact],
    source_by_key: dict[str, set[str]] | None = None,
) -> ContextCoverageReport:
    selected_text = "\n".join(f"{artefact.source_path}\n{artefact.content}" for artefact in selected).lower()
    covered_facets = [facet.value for facet in profile.facets if facet.value.lower() in selected_text]
    missing_constraints = [
        constraint.value
        for constraint in profile.constraints
        if constraint.kind != "restrictive_language" and constraint.required and constraint.value.lower() not in selected_text
    ]
    direct_evidence_count = sum(1 for artefact in selected if _direct_match_count(profile, artefact) > 0)
    source_sets = [source_by_key.get(artefact.key, set()) for artefact in selected] if source_by_key else []
    source_agreement = sum(len(sources) for sources in source_sets) / max(1, len(source_sets))
    conflict_pair_coverage = profile.need != "conflict_resolution" or _has_conflict_pair(selected_text)
    compactness_risk = "high" if len(selected) > max(8, profile.expected_evidence_count * 3) else "medium" if len(selected) > 6 else "low"
    gap_flags: list[str] = []
    if missing_constraints:
        gap_flags.append("missing_constraints")
    if len(covered_facets) < min(len(profile.facets), profile.expected_evidence_count):
        gap_flags.append("facet_coverage_weak")
    if profile.need == "conflict_resolution" and not conflict_pair_coverage:
        gap_flags.append("conflict_pair_missing")
    actionability = "full"
    if gap_flags:
        actionability = "weak"
    if not selected:
        actionability = "none"
    if profile.need == "conflict_resolution" and conflict_pair_coverage:
        actionability = "conflict"
    return ContextCoverageReport(
        covered_facets=covered_facets,
        missing_constraints=missing_constraints,
        direct_evidence_count=direct_evidence_count,
        conflict_pair_coverage=conflict_pair_coverage,
        actionability=actionability,
        source_agreement=min(1.0, source_agreement / 2.0),
        weak_expansion_dependency=any(
            source_by_key and source_by_key.get(artefact.key) == {"generated_query_variants"} for artefact in selected
        ),
        compactness_risk=compactness_risk,
        gap_flags=gap_flags,
    )


def build_prepared_context_diagnostics(
    profile: ContextPreparationProfile,
    *,
    source_by_key: dict[str, set[str]],
    selected: list[Artefact],
    fused_candidate_count: int,
    latency_ms: float,
) -> PreparedContextDiagnostics:
    selected_keys = {artefact.key for artefact in selected}
    source_counter: Counter[str] = Counter()
    selected_counter: Counter[str] = Counter()
    for key, sources in source_by_key.items():
        for source in sources:
            source_counter[source] += 1
            if key in selected_keys:
                selected_counter[source] += 1
    constraints = [constraint.value for constraint in profile.constraints]
    coverage = build_coverage_report(profile, selected, source_by_key)
    return PreparedContextDiagnostics(
        profile=profile,
        source_counts=[
            ContextSourceStats(source=source, candidate_count=count, selected_count=selected_counter.get(source, 0))
            for source, count in sorted(source_counter.items())
        ],
        fused_candidate_count=fused_candidate_count,
        selected_count=len(selected),
        hard_constraints_extracted=constraints,
        hard_constraints_satisfied=[value for value in constraints if value not in coverage.missing_constraints],
        hard_constraints_missing=coverage.missing_constraints,
        coverage=coverage,
        weak_expansion_dependency=coverage.weak_expansion_dependency,
        truncation_risk=coverage.truncation_risk,
        latency_ms=latency_ms,
        compactness_score=1.0 if coverage.compactness_risk == "low" else 0.65 if coverage.compactness_risk == "medium" else 0.35,
        provenance_coverage=sum(1 for artefact in selected if source_by_key.get(artefact.key)) / max(1, len(selected)),
    )


def _infer_archetype(lowered: str):
    if re.search(r"\b(file|symbol|function|class|module|bug|test|diff|pr|commit|implementation|dependency|stack trace)\b", lowered):
        return "developer"
    if re.search(r"\b(draft|outline|tone|audience|rewrite|section|claim|essay|article|copy)\b", lowered):
        return "writer"
    if re.search(r"\b(paper|study|dataset|method|citation|literature|hypothesis|experiment|result)\b", lowered):
        return "researcher"
    if re.search(r"\b(incident|runbook|sla|handoff|customer|support|ops|oncall|status|risk)\b", lowered):
        return "operator"
    return "general"


def _infer_need(lowered: str, archetype: str):
    if re.search(r"\b(conflict|contradict|superseded|outdated|latest|current|newer|older|reconcile)\b", lowered):
        return "conflict_resolution"
    if re.search(r"\b(not found|unavailable|missing|is there|do we have|absence|not available|cannot find)\b", lowered):
        return "absence_check"
    if archetype == "developer" and re.search(r"\b(implement|fix|debug|change|affected|callers|tests?|dependency|regression)\b", lowered):
        return "implementation_support"
    if archetype == "writer":
        return "creative_grounding"
    if archetype == "researcher":
        return "research_mapping"
    if re.search(r"\b(all|every|list|compare|across|summarize|synthesize|count|which .* and|what .* and)\b", lowered):
        return "multi_source_synthesis"
    if re.search(r"\b(decide|decision|recommend|tradeoff|risk|should we|plan)\b", lowered):
        return "decision_support"
    if re.search(r"\b(where|defined|introduced|implemented|exact|which file|what is)\b", lowered):
        return "direct_reference"
    if re.search(r"\b(continue|next|finish|carry on|pick up)\b", lowered):
        return "task_continuation"
    return "evidence_gathering"


def _extract_constraints(prompt: str) -> list[ContextConstraint]:
    constraints: list[ContextConstraint] = []
    for path in _PATH_RE.findall(prompt):
        constraints.append(ContextConstraint(kind="path", value=path))
    for quoted in _QUOTED_RE.findall(prompt):
        constraints.append(ContextConstraint(kind="quoted_reference", value=quoted.strip()))
    for match in _DATE_RE.finditer(prompt):
        constraints.append(ContextConstraint(kind="date", value=match.group(0)))
    for match in _RESTRICTIVE_RE.finditer(prompt):
        constraints.append(ContextConstraint(kind="restrictive_language", value=match.group(0).lower()))
    return _dedupe_constraints(constraints)


def _extract_facets(prompt: str, constraints: list[ContextConstraint]) -> list[ContextFacet]:
    constrained_values = {constraint.value.lower() for constraint in constraints}
    facets: list[ContextFacet] = []
    for token in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{3,}\b", prompt):
        lowered = token.lower()
        if lowered in constrained_values or lowered in {"what", "where", "when", "which", "does", "with", "from", "that", "this", "should"}:
            continue
        required = any(char.isupper() for char in token[1:]) or "_" in token or "-" in token
        facets.append(ContextFacet(name="term", value=token, required=required))
    return _dedupe_facets(facets[:16])


def _extract_source_hints(lowered: str) -> list[str]:
    hints = []
    for hint in ("slack", "gmail", "email", "github", "jira", "linear", "confluence", "docs", "meeting", "runbook", "calendar"):
        if hint in lowered:
            hints.append(hint)
    return hints


def _expected_evidence_count(lowered: str, need: str) -> int:
    if need in {"multi_source_synthesis", "research_mapping"}:
        return 4
    if need == "conflict_resolution":
        return 2
    if re.search(r"\b(all|every|complete|completeness|list)\b", lowered):
        return 5
    return 1


def _constraint_terms(prompt: str) -> set[str]:
    return {match.group(0).lower() for match in _RESTRICTIVE_RE.finditer(prompt)}


def _direct_match_count(profile: ContextPreparationProfile, artefact: Artefact) -> int:
    text = f"{artefact.source_path}\n{artefact.content}".lower()
    return sum(1 for facet in profile.facets if facet.value.lower() in text)


def _has_conflict_pair(text: str) -> bool:
    current = any(term in text for term in ("current", "latest", "updated", "newer", "now"))
    superseded = any(term in text for term in ("superseded", "old", "older", "deprecated", "previous"))
    return current and superseded


def _dedupe_constraints(constraints: list[ContextConstraint]) -> list[ContextConstraint]:
    result = []
    seen = set()
    for constraint in constraints:
        key = (constraint.kind, constraint.value.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(constraint)
    return result


def _dedupe_facets(facets: list[ContextFacet]) -> list[ContextFacet]:
    result = []
    seen = set()
    for facet in facets:
        key = facet.value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(facet)
    return result
