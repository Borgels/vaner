# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from vaner.broker.context_preparation import extract_query_keywords
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.context_preparation import ContextPreparationProfile, ContextToolTrace


def build_source_aggregation(
    prompt: str,
    candidates: list[Artefact],
    profile: ContextPreparationProfile,
    *,
    source_by_key: dict[str, set[str]] | None = None,
    max_sources: int = 40,
    provenance_budget: int | None = None,
) -> tuple[Artefact | None, ContextToolTrace]:
    """Compress many relevant sources into a provenance-preserving evidence artefact."""

    trace = ContextToolTrace(tool="aggregate_sources", input_count=len(candidates))
    if profile.need != "multi_source_synthesis" or len(candidates) < 3:
        trace.notes.append("skipped:not_multi_source_or_too_few_candidates")
        return None, trace

    source_candidates = _dedupe_candidates(candidates)[:max_sources]
    if len(source_candidates) < 3:
        trace.notes.append("skipped:dedupe_left_too_few_candidates")
        return None, trace

    resolved_provenance_budget = max_sources if provenance_budget is None else provenance_budget
    extraction_spec = _infer_extraction_spec(prompt)
    candidate_scope_spec = _infer_candidate_scope_spec(prompt, source_candidates)
    eligible_sources, excluded_sources = _apply_candidate_scope(source_candidates, candidate_scope_spec)
    aggregation_sources = eligible_sources or source_candidates
    grouped, extracted_rows = _group_findings(prompt, aggregation_sources, extraction_spec)
    extraction_source_keys = list(dict.fromkeys(row.source_key for row in extracted_rows))
    extraction_source_paths = list(dict.fromkeys(row.source_path for row in extracted_rows))
    facets = _facet_coverage(profile, aggregation_sources)
    source_classes = Counter(_source_class(artefact.source_path) for artefact in aggregation_sources)
    source_refs = _source_refs(aggregation_sources, source_by_key or {}, provenance_budget=resolved_provenance_budget)
    gaps = _coverage_gaps(prompt, profile, aggregation_sources, grouped)
    if not eligible_sources and excluded_sources:
        gaps.append("scope filter produced no eligible sources; used unfiltered candidates")
    provenance_truncated = len(aggregation_sources) > resolved_provenance_budget
    if provenance_truncated:
        gaps.append("provenance truncated to budget")

    sections = [
        "Prepared source aggregation",
        f"Question: {prompt.strip()}",
        "",
        "Coverage:",
        f"- sources_considered: {len(source_candidates)}",
        f"- eligible_sources: {len(aggregation_sources)}",
        f"- excluded_sources: {len(excluded_sources)}",
        "- source_classes: " + _format_counter(source_classes, limit=8),
        "- facets_covered: " + (", ".join(facets) if facets else "none detected"),
        f"- provenance_coverage: {min(len(aggregation_sources), resolved_provenance_budget)}/{len(aggregation_sources)}",
        "- gaps: " + ("; ".join(gaps) if gaps else "none detected"),
        "",
        "Extraction:",
        f"- item_type: {extraction_spec.item_type}",
        f"- group_by: {extraction_spec.group_by}",
        f"- count_basis: {extraction_spec.count_basis}",
        f"- extracted_row_count: {len(extracted_rows)}",
        "",
        "Grouped findings:",
        *_finding_lines(grouped),
        "",
        "Representative provenance:",
        *source_refs,
    ]
    content = "\n".join(sections).strip()
    digest = hashlib.sha256((prompt + "\n" + "\n".join(a.key for a in aggregation_sources)).encode("utf-8")).hexdigest()[:16]
    metadata = {
        "context_sources": ["aggregate_sources"],
        "aggregation_source_count": len(aggregation_sources),
        "aggregation_source_keys": [artefact.key for artefact in aggregation_sources[:resolved_provenance_budget]],
        "aggregation_source_paths": [artefact.source_path for artefact in aggregation_sources[:resolved_provenance_budget]],
        "candidate_pool_count": len(source_candidates),
        "eligible_source_count": len(aggregation_sources),
        "eligible_source_keys": [artefact.key for artefact in aggregation_sources[:resolved_provenance_budget]],
        "eligible_source_paths": [artefact.source_path for artefact in aggregation_sources[:resolved_provenance_budget]],
        "excluded_source_count": len(excluded_sources),
        "excluded_sources": [_excluded_source_metadata(item) for item in excluded_sources[:resolved_provenance_budget]],
        "candidate_scope_spec": _candidate_scope_spec_metadata(candidate_scope_spec),
        "aggregation_groups": [
            _group_metadata(group, evidence_budget=min(12, resolved_provenance_budget)) for group in grouped[:12]
        ],
        "aggregation_mode": _aggregation_mode(prompt),
        "aggregation_extraction_spec": _extraction_spec_metadata(extraction_spec),
        "count_basis": extraction_spec.count_basis,
        "extracted_row_count": len(extracted_rows),
        "extracted_rows": [_extracted_row_metadata(row) for row in extracted_rows[:resolved_provenance_budget]],
        "extraction_confidence": _mean_confidence(extracted_rows),
        "extraction_source_count": len(extraction_source_keys),
        "extraction_source_keys": extraction_source_keys[:resolved_provenance_budget],
        "extraction_source_paths": extraction_source_paths[:resolved_provenance_budget],
        "provenance_coverage_count": min(len(aggregation_sources), resolved_provenance_budget),
        "provenance_truncated": provenance_truncated,
        "provenance": "prepared_context_aggregation",
    }
    trace.output_count = 1
    trace.notes.append(f"sources_considered:{len(source_candidates)}")
    trace.notes.append(f"eligible_sources:{len(aggregation_sources)}")
    trace.notes.append(f"excluded_sources:{len(excluded_sources)}")
    trace.notes.append(f"groups:{len(grouped)}")
    trace.notes.append(f"count_basis:{extraction_spec.count_basis}")
    trace.notes.append(f"item_type:{extraction_spec.item_type}")
    trace.notes.append(f"extracted_rows:{len(extracted_rows)}")
    trace.notes.append(f"extraction_sources:{len(extraction_source_keys)}")
    return (
        Artefact(
            key=f"prepared_context:source_aggregation:{digest}",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path=f"prepared_context/source_aggregation/{digest}.md",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="vaner-context-tools",
            content=content,
            metadata=metadata,
        ),
        trace,
    )


def _dedupe_candidates(candidates: list[Artefact]) -> list[Artefact]:
    seen: set[str] = set()
    deduped: list[Artefact] = []
    for artefact in candidates:
        if artefact.key in seen:
            continue
        seen.add(artefact.key)
        deduped.append(artefact)
    return deduped


def _source_class(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 3 and parts[2].lower() in {"postmortems", "runbooks", "incidents", "tickets"}:
        return "/".join(parts[:3])
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0] if parts else "unknown"


def _facet_coverage(profile: ContextPreparationProfile, candidates: list[Artefact]) -> list[str]:
    text = "\n".join(f"{artefact.source_path}\n{artefact.content}" for artefact in candidates).lower()
    covered = [facet.value for facet in profile.facets if facet.value.lower() in text]
    return list(dict.fromkeys(covered))[:16]


def _infer_candidate_scope_spec(prompt: str, candidates: list[Artefact]) -> CandidateScopeSpec:
    required_facets = _required_scope_facets(prompt)
    source_class = _dominant_source_class(candidates)
    confidence = 0.72 if required_facets else 0.55
    return CandidateScopeSpec(
        source_class=source_class,
        required_facets=required_facets,
        optional_facets=extract_query_keywords(prompt, limit=8),
        exclusion_rules=["missing_required_facets"] if required_facets else [],
        confidence=confidence,
    )


def _required_scope_facets(prompt: str) -> list[str]:
    lower = prompt.lower()
    facets: list[str] = []
    project_match = re.search(
        r"\b(?:project|initiative|program|customer|client|account|system|service|component|area)\s+([A-Z][A-Za-z0-9_-]{2,})\b",
        prompt,
    )
    if project_match:
        facets.append(project_match.group(1))
    quoted = re.findall(r"['\"]([^'\"]{3,64})['\"]", prompt)
    facets.extend(quoted)
    if "postmortem" in lower or "postmortems" in lower:
        facets.append("postmortem")
    if "design review" in lower or "design reviews" in lower:
        facets.append("design review")
    if "customer feedback" in lower:
        facets.append("customer feedback")
    if "meeting notes" in lower:
        facets.append("meeting notes")
    return list(dict.fromkeys(facets))[:8]


def _dominant_source_class(candidates: list[Artefact]) -> str | None:
    if not candidates:
        return None
    counts = Counter(_source_class(artefact.source_path) for artefact in candidates)
    source_class, count = counts.most_common(1)[0]
    return source_class if count >= max(2, len(candidates) // 3) else None


def _apply_candidate_scope(
    candidates: list[Artefact],
    scope_spec: CandidateScopeSpec,
) -> tuple[list[Artefact], list[ExcludedAggregationSource]]:
    eligible: list[Artefact] = []
    excluded: list[ExcludedAggregationSource] = []
    for artefact in candidates:
        reasons = _scope_exclusion_reasons(artefact, scope_spec)
        if reasons:
            excluded.append(ExcludedAggregationSource(source_key=artefact.key, source_path=artefact.source_path, reasons=reasons))
        else:
            eligible.append(artefact)
    return eligible, excluded


def _scope_exclusion_reasons(artefact: Artefact, scope_spec: CandidateScopeSpec) -> list[str]:
    reasons: list[str] = []
    haystack = f"{artefact.source_path}\n{artefact.content}".lower()
    for facet in scope_spec.required_facets:
        if facet.lower() not in haystack:
            reasons.append(f"missing_required_facet:{facet}")
    if scope_spec.source_class and _source_class(artefact.source_path) != scope_spec.source_class:
        reasons.append(f"outside_source_class:{scope_spec.source_class}")
    return reasons


def _candidate_scope_spec_metadata(spec: CandidateScopeSpec) -> dict[str, object]:
    return {
        "source_class": spec.source_class,
        "required_facets": spec.required_facets,
        "optional_facets": spec.optional_facets,
        "time_window": spec.time_window,
        "entities": spec.entities,
        "source_status": spec.source_status,
        "canonicality": spec.canonicality,
        "exclusion_rules": spec.exclusion_rules,
        "max_scope_strategy": spec.max_scope_strategy,
        "confidence": spec.confidence,
    }


def _excluded_source_metadata(item: ExcludedAggregationSource) -> dict[str, object]:
    return {
        "source_key": item.source_key,
        "source_path": item.source_path,
        "reasons": item.reasons,
    }


@dataclass(frozen=True)
class AggregationExtractionSpec:
    item_type: str = "generic_theme"
    scope_hints: list[str] = field(default_factory=list)
    group_by: str = "theme"
    count_basis: str = "source_count"
    evidence_required: list[str] = field(
        default_factory=lambda: [
            "source_key",
            "source_path",
            "snippet",
            "extracted_text",
            "normalized_group",
            "confidence",
        ]
    )


@dataclass(frozen=True)
class CandidateScopeSpec:
    source_class: str | None = None
    required_facets: list[str] = field(default_factory=list)
    optional_facets: list[str] = field(default_factory=list)
    time_window: str | None = None
    entities: list[str] = field(default_factory=list)
    source_status: str | None = None
    canonicality: str = "prefer_canonical"
    exclusion_rules: list[str] = field(default_factory=list)
    max_scope_strategy: str = "facet_intersection"
    confidence: float = 0.55


@dataclass(frozen=True)
class ExcludedAggregationSource:
    source_key: str
    source_path: str
    reasons: list[str]


@dataclass(frozen=True)
class AggregationEvidence:
    source_key: str
    path: str
    snippet: str
    confidence: float = 0.7


@dataclass(frozen=True)
class ExtractedAggregationRow:
    item_type: str
    extracted_text: str
    source_key: str
    source_path: str
    snippet: str
    group_type: str
    group_raw_value: str
    group_normalized_value: str
    status: str | None = None
    date: str | None = None
    confidence: float = 0.7


@dataclass(frozen=True)
class AggregationGroup:
    value: str
    count: int
    sources: list[Artefact]
    evidence: list[AggregationEvidence]
    count_basis: str = "source_count"
    rows: list[ExtractedAggregationRow] = field(default_factory=list)


def _coverage_gaps(
    prompt: str,
    profile: ContextPreparationProfile,
    candidates: list[Artefact],
    grouped: list[AggregationGroup],
) -> list[str]:
    text = "\n".join(f"{artefact.source_path}\n{artefact.content}" for artefact in candidates).lower()
    gaps: list[str] = []
    for constraint in profile.constraints:
        if constraint.kind != "restrictive_language" and constraint.required and constraint.value.lower() not in text:
            gaps.append(f"missing required constraint: {constraint.value}")
    if _asks_for_grouped_counts(prompt) and not grouped:
        gaps.append("no stable grouping/count signal found")
    if len(candidates) >= 30:
        gaps.append("aggregation truncated to source budget")
    return gaps[:8]


def _group_findings(
    prompt: str,
    candidates: list[Artefact],
    extraction_spec: AggregationExtractionSpec,
) -> tuple[list[AggregationGroup], list[ExtractedAggregationRow]]:
    if extraction_spec.count_basis == "extracted_item_count":
        rows = _extract_rows(candidates, extraction_spec)
        item_groups = _group_extracted_rows(candidates, rows, extraction_spec)
        if item_groups:
            return item_groups, rows
    prompt_keywords = set(extract_query_keywords(prompt, limit=16))
    group_to_sources: dict[str, list[Artefact]] = defaultdict(list)
    group_to_evidence: dict[str, list[AggregationEvidence]] = defaultdict(list)
    for artefact in candidates:
        values = _group_values(artefact.content)
        if not values and _asks_for_theme_summary(prompt):
            values = _theme_values(artefact.content, prompt_keywords)
        for value in sorted(values):
            if artefact not in group_to_sources[value]:
                group_to_sources[value].append(artefact)
                group_to_evidence[value].append(
                    AggregationEvidence(
                        source_key=artefact.key,
                        path=artefact.source_path,
                        snippet=_best_snippet(artefact.content, value),
                        confidence=0.65,
                    )
                )
    grouped = [
        AggregationGroup(
            value=value,
            count=len(sources),
            sources=sources,
            evidence=group_to_evidence[value],
            count_basis=extraction_spec.count_basis,
        )
        for value, sources in group_to_sources.items()
    ]
    return sorted(grouped, key=lambda item: (-item.count, item.value.lower())), []


def _group_extracted_rows(
    candidates: list[Artefact],
    rows: list[ExtractedAggregationRow],
    extraction_spec: AggregationExtractionSpec,
) -> list[AggregationGroup]:
    source_lookup = {artefact.key: artefact for artefact in candidates}
    group_to_sources: dict[str, list[Artefact]] = defaultdict(list)
    group_to_evidence: dict[str, list[AggregationEvidence]] = defaultdict(list)
    group_to_rows: dict[str, list[ExtractedAggregationRow]] = defaultdict(list)
    group_counts: Counter[str] = Counter()
    for row in rows:
        value = row.group_normalized_value
        artefact = source_lookup.get(row.source_key)
        group_counts[value] += 1
        if artefact is not None and artefact not in group_to_sources[value]:
            group_to_sources[value].append(artefact)
        group_to_rows[value].append(row)
        group_to_evidence[value].append(
            AggregationEvidence(
                source_key=row.source_key,
                path=row.source_path,
                snippet=row.snippet,
                confidence=row.confidence,
            )
        )
    grouped = [
        AggregationGroup(
            value=value,
            count=count,
            sources=group_to_sources[value],
            evidence=group_to_evidence[value],
            count_basis=extraction_spec.count_basis,
            rows=group_to_rows[value],
        )
        for value, count in group_counts.items()
    ]
    return sorted(grouped, key=lambda item: (-item.count, item.value.lower()))


def _extract_rows(candidates: list[Artefact], extraction_spec: AggregationExtractionSpec) -> list[ExtractedAggregationRow]:
    scoped_section_available = _has_matching_section(candidates, extraction_spec.scope_hints)
    rows: list[ExtractedAggregationRow] = []
    for artefact in candidates:
        for line in _candidate_item_lines(artefact.content, extraction_spec, scoped_section_available=scoped_section_available):
            for raw_group, normalized_group in _owner_values_from_line(line):
                rows.append(
                    ExtractedAggregationRow(
                        item_type=extraction_spec.item_type,
                        extracted_text=line,
                        source_key=artefact.key,
                        source_path=artefact.source_path,
                        snippet=line[:260],
                        group_type=extraction_spec.group_by,
                        group_raw_value=raw_group,
                        group_normalized_value=normalized_group,
                        status=_status_from_line(line),
                        date=_date_from_line(line),
                        confidence=0.84 if scoped_section_available else 0.76,
                    )
                )
    return rows


def _candidate_item_lines(
    content: str,
    extraction_spec: AggregationExtractionSpec,
    *,
    scoped_section_available: bool,
) -> list[str]:
    lines: list[str] = []
    if scoped_section_available and extraction_spec.scope_hints:
        for section_line in _section_lines_matching(content, extraction_spec.scope_hints):
            if _line_has_grouping(section_line):
                lines.append(section_line)
        return lines

    in_followup_section = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^#{1,4}\s+follow[- ]?up action items\b", line, re.IGNORECASE):
            in_followup_section = True
            continue
        if in_followup_section and line.startswith("#"):
            in_followup_section = False
        if (
            not in_followup_section
            and not re.match(r"^(?:[-*]|\d+[.)])\s+", line)
            and not _line_matches_item_type(line, extraction_spec)
        ):
            continue
        if not _line_has_grouping(line):
            continue
        lines.append(line)
    return lines


def _has_matching_section(candidates: list[Artefact], hints: list[str]) -> bool:
    if not hints:
        return False
    return any(_section_lines_matching(artefact.content, hints) for artefact in candidates)


def _section_lines_matching(content: str, hints: list[str]) -> list[str]:
    matched_lines: list[str] = []
    in_matching_section = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = _heading_text(line)
        if heading is not None:
            in_matching_section = any(hint.lower() in heading.lower() for hint in hints)
            continue
        if in_matching_section:
            matched_lines.append(line)
    return matched_lines


def _heading_text(line: str) -> str | None:
    match = re.match(r"^#{1,6}\s+(.+)$", line)
    if match:
        return match.group(1).strip()
    match = re.match(r"^h[1-6]\.\s+(.+)$", line, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _line_matches_item_type(line: str, extraction_spec: AggregationExtractionSpec) -> bool:
    pattern = _ITEM_TYPE_PATTERNS.get(extraction_spec.item_type)
    if pattern is None:
        return True
    return bool(re.search(pattern, line, re.IGNORECASE))


def _line_has_grouping(line: str) -> bool:
    return bool(re.search(r"\b(owner|owning|assigned|team|dri|responsible)\b", line, re.IGNORECASE))


def _owners_from_line(line: str) -> set[str]:
    return {normalized for _raw, normalized in _owner_values_from_line(line)}


def _owner_values_from_line(line: str) -> list[tuple[str, str]]:
    owners: set[tuple[str, str]] = set()
    patterns = (
        r"\bowning team\s*:\s*([^.;\n]+)",
        r"\bowner team\s*:\s*([^.;\n]+)",
        r"\bowner\s*:\s*([^.;\n]+)",
        r"\bassigned to\s+([^.;\n]+)",
        r"\bresponsible team\s*:\s*([^.;\n]+)",
        r"\bdri\s*:\s*([^.;\n]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, line, re.IGNORECASE):
            owners.update(_split_group_value_pairs(match.group(1)))
    return sorted(owners, key=lambda item: item[1].lower())


def _group_values(content: str) -> set[str]:
    values: set[str] = set()
    patterns = (
        r"\b(?:owner|owned by|assigned to|responsible team|team|teams|dri|dris)\s*[:=-]?\s*(?:the\s+)?([A-Za-z][A-Za-z0-9&/_ -]{1,48})",
        r"\b([A-Za-z][A-Za-z0-9&/_-]{1,32})\s+(?:team|squad|group)\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, content, flags=re.IGNORECASE):
            values.update(_split_group_value(match.group(1)))
    return {value for value in values if _valid_group_value(value)}


def _split_group_value(raw: str) -> set[str]:
    return {normalized for _raw, normalized in _split_group_value_pairs(raw)}


def _split_group_value_pairs(raw: str) -> set[tuple[str, str]]:
    cleaned = re.split(r"[.;()\n]", raw.strip(), maxsplit=1)[0]
    cleaned = re.split(r"\s+\b(?:after|for|due|priority|target)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    parts = re.split(r"\s*(?:,|/|\band\b|&)\s*", cleaned, flags=re.IGNORECASE)
    values: set[tuple[str, str]] = set()
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        normalized = _normalize_group_value(stripped)
        if _valid_group_value(normalized):
            values.add((stripped, normalized))
    return values


def _normalize_group_value(value: str) -> str:
    tokens = [token for token in re.findall(r"[A-Za-z0-9_-]+", value) if token.lower() not in _GROUP_STOPWORDS]
    while len(tokens) > 1 and tokens[0].lower() in _GROUP_PREFIX_STOPWORDS:
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1].lower() in _GROUP_PREFIX_STOPWORDS:
        tokens.pop()
    if not tokens:
        return ""
    return " ".join(token.upper() if token.isupper() else token[:1].upper() + token[1:].lower() for token in tokens[:4])


def _valid_group_value(value: str) -> bool:
    lowered = value.lower()
    return bool(value) and len(value) <= 48 and lowered not in _GROUP_STOPWORDS and not lowered.isdigit()


def _theme_values(content: str, prompt_keywords: set[str]) -> set[str]:
    tokens = [
        token.lower()
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{3,}\b", content)
        if token.lower() not in prompt_keywords and token.lower() not in _THEME_STOPWORDS
    ]
    counts = Counter(tokens)
    return {token for token, count in counts.most_common(5) if count >= 2}


def _asks_for_grouped_counts(prompt: str) -> bool:
    return bool(re.search(r"\b(count|how many|most|highest number|by team|by owner|which team|which owner)\b", prompt, re.IGNORECASE))


def _asks_for_theme_summary(prompt: str) -> bool:
    return bool(re.search(r"\b(patterns?|themes?|summarize|synthesize|compare|across all)\b", prompt, re.IGNORECASE))


def _finding_lines(grouped: list[AggregationGroup]) -> list[str]:
    if not grouped:
        return ["- no grouped finding could be inferred from candidate sources"]
    lines: list[str] = []
    for group in grouped[:12]:
        unit = "item(s)" if group.count_basis == "extracted_item_count" else "source(s)"
        paths = ", ".join(artefact.source_path for artefact in group.sources[:3])
        if len(group.sources) > 3:
            paths += f", +{len(group.sources) - 3} more"
        lines.append(f"- {group.value}: {group.count} {unit}; examples: {paths}")
    return lines


def _group_metadata(group: AggregationGroup, *, evidence_budget: int) -> dict[str, object]:
    evidence = group.evidence[:evidence_budget]
    return {
        "value": group.value,
        "count": group.count,
        "count_basis": group.count_basis,
        "source_keys": [artefact.key for artefact in group.sources],
        "source_paths": [artefact.source_path for artefact in group.sources],
        "evidence": [
            {
                "source_key": item.source_key,
                "path": item.path,
                "snippet": item.snippet,
                "confidence": item.confidence,
            }
            for item in evidence
        ],
        "extracted_rows": [_extracted_row_metadata(row) for row in group.rows[:evidence_budget]],
        "evidence_truncated": len(group.evidence) > len(evidence),
    }


def _source_refs(candidates: list[Artefact], source_by_key: dict[str, set[str]], *, provenance_budget: int) -> list[str]:
    refs: list[str] = []
    for index, artefact in enumerate(candidates[:provenance_budget], start=1):
        source_names = ",".join(sorted(source_by_key.get(artefact.key, set()))) or "candidate"
        snippet = " ".join(artefact.content.split())[:220]
        refs.append(f"- [{index}] {artefact.source_path} ({source_names}): {snippet}")
    return refs


def _format_counter(counter: Counter[str], *, limit: int) -> str:
    if not counter:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in counter.most_common(limit))


def _aggregation_mode(prompt: str) -> str:
    spec = _infer_extraction_spec(prompt)
    if spec.count_basis == "extracted_item_count":
        return "count_extracted_items"
    if _asks_for_grouped_counts(prompt):
        return "count_mentions"
    if _asks_for_theme_summary(prompt):
        return "summarize_themes"
    return "group_by_entity"


def _infer_extraction_spec(prompt: str) -> AggregationExtractionSpec:
    lower = prompt.lower()
    item_type = "generic_theme"
    item_patterns = {
        "action_item": r"\b(action items?|follow[- ]?up tasks?|tasks?|tickets?|open items?)\b",
        "decision": r"\b(decisions?|decided|approved|adopted)\b",
        "risk": r"\b(risks?|blockers?|concerns?)\b",
        "customer_request": r"\b(customer requests?|feature requests?|requests?)\b",
        "open_question": r"\b(open questions?|questions?)\b",
        "meeting_time": r"\b(meeting time|scheduled|calendar|invite|time window)\b",
    }
    for candidate_type, pattern in item_patterns.items():
        if re.search(pattern, lower):
            item_type = candidate_type
            break

    scope_hints: list[str] = []
    scope_terms = {
        "follow-up": r"\bfollow[- ]?up\b",
        "risk": r"\brisks?\b",
        "owner": r"\bowners?\b",
        "deadline": r"\b(deadlines?|due dates?)\b",
    }
    for value, pattern in scope_terms.items():
        if re.search(pattern, lower):
            scope_hints.append(value)
    if not scope_hints and item_type != "generic_theme":
        scope_hints.extend(_ITEM_TYPE_SECTION_HINTS.get(item_type, []))

    if re.search(r"\b(owner|dri|assignee)\b", lower):
        group_by = "owner"
    elif re.search(r"\b(team|teams)\b", lower):
        group_by = "team"
    elif "status" in lower:
        group_by = "status"
    elif re.search(r"\b(date|when|deadline|due)\b", lower):
        group_by = "date"
    elif "source" in lower:
        group_by = "source"
    else:
        group_by = "theme"

    asks_for_item_rollup = item_type != "generic_theme" and (
        _asks_for_grouped_counts(prompt) or re.search(r"\b(list|show|summarize|across all|by)\b", lower)
    )
    if asks_for_item_rollup:
        count_basis = "extracted_item_count"
    elif _asks_for_grouped_counts(prompt):
        count_basis = "mention_count"
    else:
        count_basis = "source_count"

    return AggregationExtractionSpec(
        item_type=item_type,
        scope_hints=list(dict.fromkeys(scope_hints)),
        group_by=group_by,
        count_basis=count_basis,
    )


def _extraction_spec_metadata(spec: AggregationExtractionSpec) -> dict[str, object]:
    return {
        "item_type": spec.item_type,
        "scope_hints": spec.scope_hints,
        "group_by": spec.group_by,
        "count_basis": spec.count_basis,
        "evidence_required": spec.evidence_required,
    }


def _extracted_row_metadata(row: ExtractedAggregationRow) -> dict[str, object]:
    return {
        "item_type": row.item_type,
        "extracted_text": row.extracted_text,
        "source_key": row.source_key,
        "source_path": row.source_path,
        "snippet": row.snippet,
        "group": {
            "type": row.group_type,
            "raw_value": row.group_raw_value,
            "normalized_value": row.group_normalized_value,
        },
        "status": row.status,
        "date": row.date,
        "confidence": row.confidence,
    }


def _status_from_line(line: str) -> str | None:
    lowered = line.lower()
    if "done" in lowered or "completed" in lowered:
        return "completed"
    if "blocked" in lowered:
        return "blocked"
    if "open" in lowered or "todo" in lowered or "pending" in lowered:
        return "open"
    return None


def _date_from_line(line: str) -> str | None:
    match = re.search(
        r"\b(?:due|target(?: date)?)\s*:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[A-Za-z]{3,9}\s+\d{1,2}(?:,\s*\d{4})?)",
        line,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    match = re.search(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b", line)
    return match.group(0) if match else None


def _mean_confidence(rows: list[ExtractedAggregationRow]) -> float | None:
    if not rows:
        return None
    return round(sum(row.confidence for row in rows) / len(rows), 3)


def _best_snippet(content: str, value: str) -> str:
    value_lower = value.lower()
    for line in content.splitlines():
        cleaned = " ".join(line.split())
        if value_lower in cleaned.lower():
            return cleaned[:260]
    return " ".join(content.split())[:260]


_GROUP_STOPWORDS = {
    "and",
    "assigned",
    "item",
    "items",
    "team",
    "teams",
    "follow",
    "follow-up",
    "action",
    "actions",
    "owner",
    "owned",
    "responsible",
    "the",
    "to",
    "for",
}

_GROUP_PREFIX_STOPWORDS = {
    "eng",
    "engineering",
}

_ITEM_TYPE_PATTERNS = {
    "action_item": r"\b(action items?|follow[- ]?up tasks?|tasks?|tickets?|open items?|owner|owning|assigned|responsible|dri)\b",
    "decision": r"\b(decisions?|decided|approved|adopted|accepted|rejected|owner|dri)\b",
    "risk": r"\b(risks?|blockers?|concerns?|mitigation|owner|dri)\b",
    "customer_request": r"\b(customer requests?|feature requests?|requests?|asks?|owner|dri|product area)\b",
    "open_question": r"\b(open questions?|questions?|owner|dri)\b",
    "meeting_time": r"\b(meeting time|scheduled|calendar|invite|time window|owner|dri)\b",
}

_ITEM_TYPE_SECTION_HINTS = {
    "action_item": ["action item", "tasks", "open items"],
    "decision": ["decision"],
    "risk": ["risk"],
    "customer_request": ["request"],
    "open_question": ["open question"],
    "meeting_time": ["meeting time", "scheduled", "calendar", "invite"],
}

_THEME_STOPWORDS = {
    "that",
    "this",
    "with",
    "from",
    "have",
    "were",
    "been",
    "their",
    "there",
    "source",
    "sources",
    "summary",
    "evidence",
}
