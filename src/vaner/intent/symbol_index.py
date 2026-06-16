# SPDX-License-Identifier: Apache-2.0
"""Lightweight symbol extraction for exact prediction targets.

This module is pure and deterministic: no DB migration, no model calls, and
only relative paths leave the scanner.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from vaner.intent.target_normalization import component_terms, normalize_component, path_component_terms

SymbolRelation = Literal["definition_match", "test_match", "usage_match", "doc_match", "generated_match"]

RELATION_PRIORITY: dict[str, int] = {
    "definition_match": 5,
    "test_match": 4,
    "usage_match": 3,
    "doc_match": 2,
    "generated_match": 1,
}

_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go"}
_DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
_GENERATED_HINTS = ("generated", ".gen.", ".generated.", "_pb2.py", ".pb.go", "vendor/", "node_modules/")
_TEST_HINTS = ("/test_", "/test/", "/tests/", ".test.", ".spec.", "_test.")
_GENERIC_LOWER_SYMBOLS = {
    "add",
    "build",
    "call",
    "check",
    "close",
    "config",
    "create",
    "delete",
    "get",
    "handle",
    "init",
    "load",
    "main",
    "model",
    "open",
    "parse",
    "process",
    "query",
    "read",
    "request",
    "resolve",
    "response",
    "route",
    "run",
    "save",
    "send",
    "set",
    "start",
    "stop",
    "update",
    "value",
    "write",
}

_TS_JS_DEF_RE = re.compile(
    r"\b(?:export\s+)?(?:async\s+)?(?:function|class|interface|type|enum|const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_TS_JS_METHOD_RE = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^)]*\)\s*[{:]")
_RUST_DEF_RE = re.compile(r"\b(?:pub\s+)?(?:fn|struct|enum|trait|type|const|static)\s+([A-Za-z_][A-Za-z0-9_]*)")
_GO_DEF_RE = re.compile(r"\b(?:func|type|const|var)\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True, slots=True)
class SymbolCandidate:
    path: str
    symbol: str
    normalized_symbol: str
    language: str
    relation: SymbolRelation
    score: float
    matched_terms: tuple[str, ...] = field(default_factory=tuple)


def symbol_candidates_for_text(
    repo_root: Path | str,
    text: str,
    *,
    available_paths: list[str] | tuple[str, ...] | None = None,
    max_files: int = 2000,
) -> tuple[SymbolCandidate, ...]:
    terms = component_terms(text)
    if not terms:
        return ()
    return symbol_candidates_for_terms(
        repo_root,
        terms,
        available_paths=available_paths,
        max_files=max_files,
    )


def symbol_candidates_for_terms(
    repo_root: Path | str,
    terms: tuple[str, ...],
    *,
    available_paths: list[str] | tuple[str, ...] | None = None,
    max_files: int = 2000,
) -> tuple[SymbolCandidate, ...]:
    root = Path(repo_root)
    paths = _candidate_paths(root, available_paths, max_files=max_files)
    candidates: list[SymbolCandidate] = []
    for rel_path in paths:
        path = root / rel_path
        lower = rel_path.lower()
        suffix = path.suffix.lower()
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        relation_hint: SymbolRelation | None = None
        if _is_generated(lower):
            relation_hint = "generated_match"
        elif suffix in _DOC_SUFFIXES:
            relation_hint = "doc_match"
        definitions = _extract_definitions(rel_path, content)
        definition_norms = {normalize_component(symbol): symbol for symbol in definitions if not _is_generic_lower_symbol(symbol)}
        path_terms = set(path_component_terms(rel_path))
        for term in terms:
            if term in definition_norms:
                relation = relation_hint or ("test_match" if _is_test(lower) else "definition_match")
                candidates.append(
                    SymbolCandidate(
                        path=rel_path,
                        symbol=definition_norms[term],
                        normalized_symbol=term,
                        language=_language_for_suffix(suffix),
                        relation=relation,
                        score=_score_for_relation(relation),
                        matched_terms=(term,),
                    )
                )
                continue
            if term in path_terms and suffix in _SOURCE_SUFFIXES:
                relation = relation_hint or ("test_match" if _is_test(lower) else "usage_match")
                candidates.append(
                    SymbolCandidate(
                        path=rel_path,
                        symbol=term,
                        normalized_symbol=term,
                        language=_language_for_suffix(suffix),
                        relation=relation,
                        score=_score_for_relation(relation) - 0.05,
                        matched_terms=(term,),
                    )
                )
                continue
            if term in normalize_component(content):
                relation = relation_hint or ("test_match" if _is_test(lower) else "usage_match")
                candidates.append(
                    SymbolCandidate(
                        path=rel_path,
                        symbol=term,
                        normalized_symbol=term,
                        language=_language_for_suffix(suffix),
                        relation=relation,
                        score=_score_for_relation(relation) - 0.10,
                        matched_terms=(term,),
                    )
                )
    return tuple(_dedupe_and_sort(candidates))


def rank_exact_paths(
    repo_root: Path | str,
    text: str,
    *,
    available_paths: list[str] | tuple[str, ...] | None = None,
    max_paths: int = 8,
) -> list[str]:
    candidates = symbol_candidates_for_text(repo_root, text, available_paths=available_paths)
    if not candidates:
        return []
    by_path: dict[str, float] = {}
    matched_terms_by_path: dict[str, set[str]] = {}
    for candidate in candidates:
        matched_terms_by_path.setdefault(candidate.path, set()).update(candidate.matched_terms)
        by_path[candidate.path] = by_path.get(candidate.path, 0.0) + _path_candidate_score(candidate)
    for path, terms in matched_terms_by_path.items():
        # Multi-term matches are usually better implementation anchors than
        # single generic word hits, especially for prompts like "reward
        # computation signals" where many signal modules mention "signal".
        by_path[path] = by_path.get(path, 0.0) + min(2.5, max(0, len(terms) - 1) * 0.45)
    ranked = sorted(by_path, key=lambda path: (-by_path[path], path))
    return ranked[:max_paths]


def _candidate_paths(root: Path, available_paths: list[str] | tuple[str, ...] | None, *, max_files: int) -> list[str]:
    if available_paths is not None:
        return [
            path for path in available_paths if (root / path).is_file() and (root / path).suffix.lower() in _SOURCE_SUFFIXES | _DOC_SUFFIXES
        ][:max_files]
    paths: list[str] = []
    for path in root.rglob("*"):
        if len(paths) >= max_files:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if "/.git/" in f"/{rel}/" or "/.vaner/" in f"/{rel}/":
            continue
        if path.suffix.lower() in _SOURCE_SUFFIXES | _DOC_SUFFIXES:
            paths.append(rel)
    return paths


def _extract_definitions(rel_path: str, content: str) -> tuple[str, ...]:
    suffix = Path(rel_path).suffix.lower()
    if suffix == ".py":
        return _extract_python_definitions(content)
    if suffix in {".ts", ".tsx", ".js", ".jsx"}:
        return tuple(_TS_JS_DEF_RE.findall(content) + _TS_JS_METHOD_RE.findall(content))
    if suffix == ".rs":
        return tuple(_RUST_DEF_RE.findall(content))
    if suffix == ".go":
        return tuple(_GO_DEF_RE.findall(content))
    return ()


def _extract_python_definitions(content: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return ()
    symbols: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    symbols.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id.isupper():
            symbols.append(node.target.id)
    return tuple(symbols)


def _dedupe_and_sort(candidates: list[SymbolCandidate]) -> list[SymbolCandidate]:
    best: dict[tuple[str, str, str], SymbolCandidate] = {}
    for candidate in candidates:
        key = (candidate.path, candidate.normalized_symbol, candidate.relation)
        existing = best.get(key)
        if existing is None or candidate.score > existing.score:
            best[key] = candidate
    return sorted(
        best.values(),
        key=lambda candidate: (
            -RELATION_PRIORITY[candidate.relation],
            -candidate.score,
            candidate.path,
            candidate.symbol,
        ),
    )


_LOW_SPECIFICITY_TERMS = {
    "combined",
    "combine",
    "computation",
    "context",
    "database",
    "extract",
    "feature",
    "final",
    "model",
    "package",
    "persist",
    "produce",
    "retrieve",
    "schema",
    "signal",
    "signals",
    "table",
    "value",
}


def _path_candidate_score(candidate: SymbolCandidate) -> float:
    relation_weight = float(RELATION_PRIORITY[candidate.relation]) * 4.0
    score = relation_weight + candidate.score
    normalized_path_terms = set(path_component_terms(candidate.path))
    matched_terms = set(candidate.matched_terms)
    basename = Path(candidate.path).stem
    normalized_basename = normalize_component(basename)
    normalized_symbol = normalize_component(candidate.symbol)

    if matched_terms & normalized_path_terms:
        score += 5.0
    if normalized_basename and normalized_basename in matched_terms:
        score += 8.0
    if normalized_symbol and normalized_symbol in matched_terms:
        score += 4.0
    if candidate.relation == "definition_match" and not candidate.symbol.startswith("_"):
        score += 3.0
    if candidate.symbol.startswith("_"):
        score -= 2.0
    if matched_terms and matched_terms <= _LOW_SPECIFICITY_TERMS and candidate.relation == "usage_match":
        score -= 12.0
    if candidate.relation == "usage_match" and not (matched_terms & normalized_path_terms):
        score -= 8.0
    if candidate.path.startswith(("src/", "lib/", "app/", "packages/")):
        score += 0.5
    if candidate.path.startswith(("tests/", "test/")):
        score -= 0.5
    return score


def _score_for_relation(relation: SymbolRelation) -> float:
    return {
        "definition_match": 1.0,
        "test_match": 0.85,
        "usage_match": 0.70,
        "doc_match": 0.45,
        "generated_match": 0.30,
    }[relation]


def _language_for_suffix(suffix: str) -> str:
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".rs": "rust",
        ".go": "go",
        ".md": "markdown",
        ".mdx": "markdown",
        ".rst": "rst",
        ".txt": "text",
    }.get(suffix, "unknown")


def _is_test(path: str) -> bool:
    return any(hint in f"/{path}" for hint in _TEST_HINTS)


def _is_generated(path: str) -> bool:
    return any(hint in path for hint in _GENERATED_HINTS)


def _is_generic_lower_symbol(symbol: str) -> bool:
    return symbol == symbol.lower() and symbol in _GENERIC_LOWER_SYMBOLS
