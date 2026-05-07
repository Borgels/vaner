# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, defaultdict

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
    provenance_budget: int = 24,
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

    grouped = _group_findings(prompt, source_candidates)
    facets = _facet_coverage(profile, source_candidates)
    source_classes = Counter(_source_class(artefact.source_path) for artefact in source_candidates)
    source_refs = _source_refs(source_candidates, source_by_key or {}, provenance_budget=provenance_budget)
    gaps = _coverage_gaps(prompt, profile, source_candidates, grouped)

    sections = [
        "Prepared source aggregation",
        f"Question: {prompt.strip()}",
        "",
        "Coverage:",
        f"- sources_considered: {len(source_candidates)}",
        "- source_classes: " + _format_counter(source_classes, limit=8),
        "- facets_covered: " + (", ".join(facets) if facets else "none detected"),
        "- gaps: " + ("; ".join(gaps) if gaps else "none detected"),
        "",
        "Grouped findings:",
        *_finding_lines(grouped),
        "",
        "Representative provenance:",
        *source_refs,
    ]
    content = "\n".join(sections).strip()
    digest = hashlib.sha256((prompt + "\n" + "\n".join(a.key for a in source_candidates)).encode("utf-8")).hexdigest()[:16]
    metadata = {
        "context_sources": ["aggregate_sources"],
        "aggregation_source_count": len(source_candidates),
        "aggregation_source_keys": [artefact.key for artefact in source_candidates[:provenance_budget]],
        "aggregation_source_paths": [artefact.source_path for artefact in source_candidates[:provenance_budget]],
        "aggregation_groups": [{"value": value, "count": count} for value, count, _ in grouped[:12]],
        "provenance": "prepared_context_aggregation",
    }
    trace.output_count = 1
    trace.notes.append(f"sources_considered:{len(source_candidates)}")
    trace.notes.append(f"groups:{len(grouped)}")
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


def _coverage_gaps(
    prompt: str,
    profile: ContextPreparationProfile,
    candidates: list[Artefact],
    grouped: list[tuple[str, int, list[Artefact]]],
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


def _group_findings(prompt: str, candidates: list[Artefact]) -> list[tuple[str, int, list[Artefact]]]:
    prompt_keywords = set(extract_query_keywords(prompt, limit=16))
    group_to_sources: dict[str, list[Artefact]] = defaultdict(list)
    for artefact in candidates:
        values = _group_values(artefact.content)
        if not values and _asks_for_theme_summary(prompt):
            values = _theme_values(artefact.content, prompt_keywords)
        for value in sorted(values):
            if artefact not in group_to_sources[value]:
                group_to_sources[value].append(artefact)
    grouped = [(value, len(sources), sources) for value, sources in group_to_sources.items()]
    return sorted(grouped, key=lambda item: (-item[1], item[0].lower()))


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
    cleaned = re.split(r"[.;()\n]", raw.strip(), maxsplit=1)[0]
    parts = re.split(r"\s*(?:,|/|\band\b|&)\s*", cleaned, flags=re.IGNORECASE)
    return {_normalize_group_value(part) for part in parts if part.strip()}


def _normalize_group_value(value: str) -> str:
    tokens = [token for token in re.findall(r"[A-Za-z0-9_-]+", value) if token.lower() not in _GROUP_STOPWORDS]
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


def _finding_lines(grouped: list[tuple[str, int, list[Artefact]]]) -> list[str]:
    if not grouped:
        return ["- no grouped finding could be inferred from candidate sources"]
    lines: list[str] = []
    for value, count, sources in grouped[:12]:
        paths = ", ".join(artefact.source_path for artefact in sources[:3])
        if len(sources) > 3:
            paths += f", +{len(sources) - 3} more"
        lines.append(f"- {value}: {count} source(s); examples: {paths}")
    return lines


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
