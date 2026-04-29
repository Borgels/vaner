# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from vaner.intent.evidence_hygiene import evidence_path_kind, filter_evidence_paths, is_evidence_path_allowed
from vaner.intent.graph import RelationshipGraph
from vaner.intent.prediction_v2 import StructuredPrediction
from vaner.intent.symbol_index import RELATION_PRIORITY, SymbolCandidate
from vaner.intent.target_normalization import component_terms, normalize_component

_STOPWORDS = {
    "about",
    "after",
    "again",
    "and",
    "between",
    "can",
    "does",
    "explain",
    "for",
    "from",
    "happens",
    "how",
    "into",
    "please",
    "that",
    "the",
    "this",
    "through",
    "what",
    "when",
    "where",
    "which",
    "with",
    "work",
    "works",
}


@dataclass(frozen=True, slots=True)
class EvidenceTarget:
    path: str
    score: float
    signals: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
    role: str = "supporting"
    role_reason: str = ""
    symbol_score: float = 0.0
    path_score: float = 0.0
    content_score: float = 0.0
    graph_score: float = 0.0
    recency_score: float = 0.0
    role_score: float = 0.0
    symbol_relation: str = ""
    wrong_primary_risk: float = 0.0
    wrong_primary_reasons: tuple[str, ...] = field(default_factory=tuple)
    readiness_reason: str = ""


def _tokens(text: str) -> set[str]:
    split_camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    parts = re.split(r"[^A-Za-z0-9_]+", split_camel.lower())
    expanded: set[str] = set()
    for part in parts:
        if len(part) < 3 or part in _STOPWORDS:
            continue
        expanded.add(part)
        expanded.update(piece for piece in re.findall(r"[a-z]+|[0-9]+", part) if len(piece) >= 3 and piece not in _STOPWORDS)
    return expanded


def _is_mechanism_query(structured: StructuredPrediction) -> bool:
    text = " ".join((structured.object, structured.semantic_hint)).lower()
    patterns = (
        "how does",
        "how do",
        "how is",
        "what happens",
        "what triggers",
        "what signals",
        "walk me through",
        "explain",
        "computed",
        "decide",
        "decides",
        "pipeline",
        "flow",
        "relationship",
    )
    return structured.action_type in {"explain", "inspect"} and any(pattern in text for pattern in patterns)


def _docs_primary_allowed(structured: StructuredPrediction) -> bool:
    text = " ".join((structured.object, structured.semantic_hint, structured.answer_shape)).lower()
    return any(term in text for term in ("architecture", "doc", "docs", "documentation", "schema", "contract", "api surface", "readme"))


def _testing_shaped(structured: StructuredPrediction) -> bool:
    text = " ".join((structured.object, structured.semantic_hint, structured.answer_shape)).lower()
    return structured.action_type == "test" or any(term in text for term in ("test", "tests", "coverage", "spec"))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _coverage(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), 8)


def _path_tokens(path: str) -> set[str]:
    pure = PurePosixPath(path)
    text = " ".join((*pure.parts, pure.stem, pure.suffix.lstrip(".")))
    return _tokens(text.replace("_", " ").replace("-", " "))


def _alias_tokens(text: str) -> set[str]:
    tokens = set(_tokens(text))
    aliases: set[str] = set(tokens)
    suffixes = ("store", "cache", "scorer", "frontier", "reasoner", "daemon", "assembler", "resolver", "registry", "policy", "engine")
    for token in tokens:
        for suffix in suffixes:
            if token.endswith(suffix) and len(token) > len(suffix) + 2:
                aliases.add(token[: -len(suffix)])
                aliases.add(suffix)
    return aliases


def _role_fit(path: str, action_type: str, answer_shape: str) -> float:
    kind = evidence_path_kind(path)
    is_test = kind == "test"
    is_doc = kind == "docs"
    is_source = kind == "source"
    is_config = kind == "config"
    if action_type == "test":
        return 1.0 if is_test else (0.65 if is_source else 0.2)
    if action_type in {"explain", "inspect", "review", "summarize"} or answer_shape in {"investigation_report", "summary"}:
        return 1.0 if is_source else (0.75 if is_config else (0.45 if is_doc else 0.15))
    if action_type in {"implement", "debug"} or answer_shape == "code_or_patch":
        return 1.0 if is_source else (0.65 if is_test else 0.25)
    return 0.55 if is_source or is_doc or is_test else 0.25


def _evidence_role(path: str, structured: StructuredPrediction, score: float) -> tuple[str, str]:
    kind = evidence_path_kind(path)
    mechanism = _is_mechanism_query(structured)
    docs_allowed = _docs_primary_allowed(structured)
    if kind in {"source", "test", "config"}:
        if mechanism and kind == "test":
            return "supporting", "tests support mechanism evidence but implementation is primary"
        return "primary", f"{kind} evidence matches predicted task"
    if kind == "docs" and docs_allowed:
        return "primary", "documentation-shaped prediction"
    if kind == "docs":
        return "supporting", "docs support but do not answer implementation mechanics alone"
    return "rejected" if score < 0.35 else "supporting", f"{kind} artifact is not primary prediction evidence"


def _graph_near_score(path: str, seed_paths: list[str], graph: RelationshipGraph | None) -> float:
    if graph is None or not seed_paths:
        return 0.0
    key = f"file:{path}"
    for seed in seed_paths[:8]:
        seed_key = f"file:{seed}"
        if key == seed_key:
            return 1.0
        if key in graph.propagate(seed_key, depth=1):
            return 0.7
        if key in graph.propagate(seed_key, depth=2):
            return 0.35
    return 0.0


def _artefact_text(path: str, artefacts_by_key: dict[str, object]) -> str:
    artefact = artefacts_by_key.get(f"file_summary:{path}") or artefacts_by_key.get(f"file:{path}")
    if artefact is None:
        return ""
    return str(getattr(artefact, "content", "") or "")[:4096]


def resolve_evidence_targets(
    structured: StructuredPrediction,
    *,
    available_paths: list[str],
    artefacts_by_key: dict[str, object] | None = None,
    graph: RelationshipGraph | None = None,
    recent_queries: list[str] | None = None,
    aligned_paths: set[str] | None = None,
    working_set: dict[str, float] | None = None,
    top_k: int = 8,
) -> list[EvidenceTarget]:
    """Resolve a structured prediction into concrete repo evidence targets."""
    artefacts_by_key = artefacts_by_key or {}
    recent_queries = recent_queries or []
    aligned_paths = aligned_paths or set()
    working_set = working_set or {}
    candidates = filter_evidence_paths(available_paths)
    explicit_targets = filter_evidence_paths(structured.evidence_targets)
    query_text = " ".join((structured.object, structured.semantic_hint, " ".join(explicit_targets), " ".join(recent_queries[-3:])))
    query_tokens = _alias_tokens(query_text)
    concrete_terms = component_terms(query_text)
    mechanism = _is_mechanism_query(structured)
    docs_allowed = _docs_primary_allowed(structured)
    testing_allowed = _testing_shaped(structured)
    # The pure symbol scanner needs real files, but this resolver often runs
    # from artefact summaries only. Build a symbol map from explicit path and
    # content evidence, then let engine callers that have real files add exact
    # paths before this resolver runs.
    symbol_by_path: dict[str, SymbolCandidate] = {}
    if concrete_terms:
        try:
            from vaner.intent.symbol_index import SymbolCandidate as _SC

            for path in candidates:
                path_terms = _alias_tokens(path)
                content_terms = _alias_tokens(_artefact_text(path, artefacts_by_key))
                compact = normalize_component(f"{path} {_artefact_text(path, artefacts_by_key)}")
                matched = {term for term in concrete_terms if term in compact or term in (path_terms | content_terms)}
                if not matched:
                    continue
                kind = evidence_path_kind(path)
                relation = "test_match" if kind == "test" else ("doc_match" if kind == "docs" else "usage_match")
                if kind == "source" and any(
                    f"class{term}" in compact or f"def{term}" in compact or term in normalize_component(path) for term in matched
                ):
                    relation = "definition_match"
                symbol_by_path[path] = _SC(
                    path=path,
                    symbol=next(iter(matched)),
                    normalized_symbol=next(iter(matched)),
                    language="unknown",
                    relation=relation,  # type: ignore[arg-type]
                    score={"definition_match": 1.0, "test_match": 0.85, "usage_match": 0.70, "doc_match": 0.45}.get(relation, 0.3),
                    matched_terms=tuple(sorted(matched)),
                )
        except Exception:
            symbol_by_path = {}
    scored: list[EvidenceTarget] = []
    for path in candidates:
        path_kind = evidence_path_kind(path)
        pure = PurePosixPath(path)
        path_tokens = _alias_tokens(" ".join((*pure.parts, pure.stem, pure.suffix.lstrip("."))))
        basename_tokens = _alias_tokens(pure.stem)
        content_tokens = _alias_tokens(_artefact_text(path, artefacts_by_key))
        path_token_score = max(_jaccard(query_tokens, path_tokens), _coverage(query_tokens, path_tokens))
        content_score = max(_jaccard(query_tokens, content_tokens), _coverage(query_tokens, content_tokens))
        explicit_score = 1.0 if path in explicit_targets else 0.0
        graph_score = _graph_near_score(path, explicit_targets, graph)
        align_score = 1.0 if path in aligned_paths else 0.0
        recency_score = 1.0 if path in working_set else 0.0
        role_score = _role_fit(path, structured.action_type, structured.answer_shape)
        symbol_candidate = symbol_by_path.get(path)
        symbol_score = float(symbol_candidate.score) if symbol_candidate is not None else 0.0
        symbol_relation = symbol_candidate.relation if symbol_candidate is not None else ""
        object_tokens = _alias_tokens(structured.object)
        object_path_score = _coverage(object_tokens, path_tokens)
        score = (
            0.38 * symbol_score
            + 0.22 * max(path_token_score, explicit_score, object_path_score)
            + 0.16 * max(content_score, explicit_score)
            + 0.10 * graph_score
            + 0.06 * align_score
            + 0.03 * recency_score
            + 0.05 * role_score
        )
        if object_path_score >= 0.5:
            score += 0.20
        if path_token_score >= 0.35 and role_score >= 0.8:
            score += 0.12
        if basename_tokens and basename_tokens & query_tokens and role_score >= 0.8:
            score += 0.35
        if mechanism and path_kind in {"source", "config"}:
            score += 0.22
        if mechanism and path_kind == "docs" and not docs_allowed:
            score = min(score, 0.42)
        if path_kind in {"generated", "data", "rejected"}:
            score = min(score, 0.30)
        signals: list[str] = []
        if explicit_score:
            signals.append("explicit_target")
        if content_score >= 0.08:
            signals.append("content")
        if path_token_score >= 0.08:
            signals.append("path")
        if graph_score:
            signals.append("graph")
        if align_score:
            signals.append("goal")
        if recency_score:
            signals.append("working_set")
        role, role_reason = _evidence_role(path, structured, score)
        wrong_reasons: list[str] = []
        if concrete_terms and not symbol_score and score >= 0.45:
            wrong_reasons.append("named_symbol_missing")
        if recency_score and not symbol_score and concrete_terms:
            wrong_reasons.append("stale_cluster_won")
        if graph_score and not (symbol_score or path_token_score >= 0.35 or content_score >= 0.10):
            wrong_reasons.append("graph_neighbor_only")
        if path_kind in {"docs", "generated"} and not docs_allowed and symbol_relation in {"doc_match", "generated_match"}:
            wrong_reasons.append("docs_or_generated_outranked_source")
        if content_score > 0 and content_score < 0.12 and not symbol_score:
            wrong_reasons.append("weak_content_overlap")
        if symbol_relation:
            signals.insert(0, symbol_relation)
        if symbol_relation == "test_match" and not testing_allowed:
            role = "supporting"
            role_reason = "test evidence requires usage or testing-shaped prediction"
        if symbol_relation in {"doc_match", "generated_match"} and not docs_allowed:
            role = "supporting"
            role_reason = "docs/generated evidence is supporting for source-shaped predictions"
        if score <= 0.04 or role == "rejected":
            continue
        scored.append(
            EvidenceTarget(
                path=path,
                score=round(min(1.0, score), 4),
                signals=tuple(signals or ("role",)),
                reason=", ".join(signals or ("role fit",)),
                role=role,
                role_reason=role_reason,
                symbol_score=round(symbol_score, 4),
                path_score=round(max(path_token_score, object_path_score, explicit_score), 4),
                content_score=round(content_score, 4),
                graph_score=round(graph_score, 4),
                recency_score=round(recency_score, 4),
                role_score=round(role_score, 4),
                symbol_relation=symbol_relation,
                wrong_primary_risk=round(min(1.0, 0.25 * len(wrong_reasons)), 4),
                wrong_primary_reasons=tuple(wrong_reasons),
            )
        )

    role_rank = {"primary": 2, "supporting": 1, "rejected": 0}
    scored.sort(
        key=lambda target: (
            RELATION_PRIORITY.get(target.symbol_relation, 0),
            role_rank.get(target.role, 0),
            target.score,
            target.path in explicit_targets,
        ),
        reverse=True,
    )
    return scored[:top_k]


def evidence_readiness(targets: list[EvidenceTarget], structured: StructuredPrediction | None = None) -> tuple[bool, str]:
    if not targets:
        return False, "no_evidence_targets"
    primary = [target for target in targets if target.role == "primary"]
    supporting = [target for target in targets if target.role == "supporting"]
    if structured is not None and _is_mechanism_query(structured) and not _docs_primary_allowed(structured) and not primary:
        return False, "no_primary_evidence"
    if structured is not None and component_terms(" ".join((structured.object, structured.semantic_hint))):
        has_definition = any(target.symbol_relation == "definition_match" and target.score >= 0.55 for target in targets)
        has_test = any(target.symbol_relation == "test_match" and target.score >= 0.45 for target in targets)
        has_usage = any(target.symbol_relation == "usage_match" and target.score >= 0.45 for target in targets)
        docs_allowed = _docs_primary_allowed(structured)
        if not (has_definition or (has_test and has_usage) or docs_allowed):
            return False, "weak_component_match"
    if not primary and supporting:
        return False, "supporting_only_evidence"
    scored_targets = primary or targets
    top = scored_targets[0].score
    if top >= 0.70:
        return True, "strong_evidence_target"
    if top >= 0.45 and len(scored_targets) >= 2:
        return True, "multiple_evidence_targets"
    return False, "weak_evidence_targets"


def evidence_noise_rate(paths: list[str]) -> float:
    if not paths:
        return 0.0
    noisy = sum(1 for path in paths if not is_evidence_path_allowed(path))
    return noisy / len(paths)
