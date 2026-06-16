# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Literal

from vaner.broker.selector import _prompt_terms
from vaner.models.artefact import Artefact
from vaner.policy.budget import count_tokens

EvidenceSpanRole = Literal[
    "direct",
    "constant_or_default",
    "schema_or_storage",
    "caller_or_downstream",
    "test_or_example",
    "supporting",
    "provenance",
]


@dataclass(frozen=True)
class EvidenceSpan:
    path: str
    start_line: int
    end_line: int
    role: EvidenceSpanRole
    symbol: str
    excerpt: str
    score: float
    protected: bool
    reason: str


def _trim_chunk_to_budget(chunk: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    if count_tokens(chunk) <= max_tokens:
        return chunk
    first_line, _, remainder = chunk.partition("\n")
    marker = "\n...[trimmed]\n"
    header = first_line.strip()
    if count_tokens(header + marker) > max_tokens:
        max_chars = max(1, max_tokens * 4)
        return (header + "\n")[:max_chars].rstrip()
    max_chars = max(120, max_tokens * 4)
    trimmed = (header + "\n" + remainder[:max_chars]).rstrip()
    while trimmed and count_tokens(trimmed + marker) > max_tokens:
        trimmed = trimmed[: max(0, len(trimmed) - 120)].rstrip()
    return trimmed + marker if trimmed else header + marker


def _chunk_for_artefact(artefact: Artefact, *, max_chunk_tokens: int) -> str:
    header = f"### {artefact.source_path}\n"
    content = artefact.content or ""
    if max_chunk_tokens <= 0:
        return header.rstrip()
    full = f"{header}{content}\n"
    if count_tokens(full) <= max_chunk_tokens:
        return full
    sections = _important_lines(content)
    if sections:
        candidate = f"{header}" + "\n".join(sections) + "\n...[compacted to important implementation anchors]\n"
        if count_tokens(candidate) <= max_chunk_tokens:
            return candidate
    return _trim_chunk_to_budget(full, max_chunk_tokens)


def _important_lines(content: str) -> list[str]:
    important: list[str] = []
    patterns = (
        r"^(Classes|Functions|Constants|Limits|Schema):",
        r"\b(CREATE TABLE|CREATE INDEX|USING fts5|INSERT INTO|SELECT .* FROM)\b",
        r"\b(class|def|async def)\s+[A-Za-z_][A-Za-z0-9_]*",
        r"\b(max_|min_|limit|threshold|timeout|ttl|budget|window|top_n|top_k)\w*\b",
    )
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(re.search(pattern, stripped, flags=re.IGNORECASE) for pattern in patterns):
            important.append(stripped[:240])
        if len(important) >= 24:
            break
    return important


def extract_evidence_spans(
    artefact: Artefact,
    query: str,
    *,
    base_score: float = 0.0,
    max_spans_per_file: int = 8,
) -> list[EvidenceSpan]:
    """Return query-aware, line-exact spans for one artefact.

    The extractor is deliberately deterministic. It preserves code and config
    ingredients as copied source spans instead of asking a model to summarize
    them, which keeps identifiers, constants, table names, and parser contracts
    exact under tight context budgets.
    """

    text = artefact.content or ""
    lines = text.splitlines()
    if not lines:
        return []
    terms = _prompt_terms(query)
    if not terms:
        terms = [part for part in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query.lower()) if len(part) > 2]
    candidates = _structured_candidates(artefact.source_path, lines, terms)
    candidates.extend(_line_candidates(artefact.source_path, lines, terms))
    candidates = _merge_candidate_windows(candidates)

    spans: list[EvidenceSpan] = []
    for start, end, role, symbol, score, reason in candidates:
        excerpt = _numbered_excerpt(lines, start, end)
        if not excerpt.strip():
            continue
        adjusted = score + (base_score * 0.08)
        spans.append(
            EvidenceSpan(
                path=artefact.source_path,
                start_line=start,
                end_line=end,
                role=role,
                symbol=symbol,
                excerpt=excerpt,
                score=adjusted,
                protected=role in {"direct", "constant_or_default", "schema_or_storage", "caller_or_downstream", "test_or_example"},
                reason=reason,
            )
        )
    spans.sort(key=lambda span: (_role_priority(span.role), span.protected, span.score, -span.start_line), reverse=True)
    return _diverse_spans(spans, max_spans_per_file=max_spans_per_file)


def _structured_candidates(
    path: str,
    lines: list[str],
    terms: list[str],
) -> list[tuple[int, int, EvidenceSpanRole, str, float, str]]:
    if not path.endswith(".py"):
        return []
    text = "\n".join(lines)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    candidates: list[tuple[int, int, EvidenceSpanRole, str, float, str]] = []
    imports = [
        node.lineno
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom)) and _node_line_score(lines[node.lineno - 1], terms, path) > 0
    ]
    if imports:
        candidates.append(
            (max(1, min(imports) - 1), min(len(lines), max(imports) + 1), "provenance", "imports", 4.0, "matched import provenance")
        )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbol = node.name
            start = node.lineno
            end = min(len(lines), getattr(node, "end_lineno", node.lineno))
            block_text = "\n".join(lines[start - 1 : end]).lower()
            score = _symbol_score(symbol, terms) + _text_term_score(block_text, terms)
            anchor = _block_anchor_score(symbol, block_text)
            if score <= 0 and anchor <= 0:
                continue
            semantic_role = _role_for_block(path, symbol, block_text)
            role = semantic_role if semantic_role != "supporting" else "direct"
            max_lines = 3 if isinstance(node, ast.ClassDef) else 42
            span_start, span_end = _focused_block_window(lines, start, end, terms, max_lines=max_lines)
            candidates.append((span_start, span_end, role, symbol, score + anchor + 3.0, f"matched {symbol} block"))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target_names = _assignment_targets(node)
            if not target_names:
                continue
            start = node.lineno
            end = min(len(lines), getattr(node, "end_lineno", node.lineno))
            line_text = "\n".join(lines[start - 1 : end])
            score = max((_symbol_score(name, terms) for name in target_names), default=0.0) + _node_line_score(line_text, terms, path)
            if score <= 0 and not _constantish(line_text):
                continue
            candidates.append(
                (
                    max(1, start - 1),
                    min(len(lines), end + 1),
                    "constant_or_default",
                    ", ".join(target_names[:3]),
                    score + 5.0,
                    "preserved constant/default assignment",
                )
            )
    return candidates


def _line_candidates(
    path: str,
    lines: list[str],
    terms: list[str],
) -> list[tuple[int, int, EvidenceSpanRole, str, float, str]]:
    candidates: list[tuple[int, int, EvidenceSpanRole, str, float, str]] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if path.endswith(".py") and stripped.startswith("#"):
            continue
        score = _node_line_score(line, terms, path)
        if score <= 0:
            continue
        role = _role_for_text(path, line)
        if path.endswith(".py"):
            radius = 0 if re.search(r"\b(class|def|async def)\s+[A-Za-z_][A-Za-z0-9_]*", line) else 1
        else:
            radius = 1 if role in {"constant_or_default", "schema_or_storage"} else 2
        symbol = _symbol_from_line(line)
        candidates.append(
            (
                max(1, index - radius),
                min(len(lines), index + radius),
                role,
                symbol,
                score,
                f"matched {role.replace('_', ' ')} line",
            )
        )
    return candidates


def _node_line_score(text: str, terms: list[str], path: str) -> float:
    lowered = text.lower()
    path_lowered = path.lower()
    score = 0.0
    content_hit_score = 0.0
    for term in terms:
        if len(term) < 3:
            continue
        if term in lowered:
            content_hit_score += 4.0 if "_" not in term else 7.0
        if term in path_lowered:
            score += 1.5
    if content_hit_score <= 0:
        score = 0.0
    score += content_hit_score
    if re.search(r"\b(class|def|async def)\s+[A-Za-z_][A-Za-z0-9_]*", text):
        score += 2.0
    if _constantish(text):
        score += 4.0
    if re.search(r"\b(CREATE TABLE|CREATE INDEX|USING fts5|ALTER TABLE|INSERT INTO|SELECT\b.+\bFROM)\b", text, re.IGNORECASE):
        score += 7.0
    if re.search(r"\b(json\.loads|json\.dumps|JSON_CONTRACT|ranked_files|follow_on|response_format|schema)\b", text):
        score += 5.0
    if re.search(r"\b(reward_total|reward_components|cache_tier|quality_lift|judge_score|similarity|raw_reward)\b", text):
        score += 5.0
    return score


def _text_term_score(text: str, terms: list[str]) -> float:
    return sum(2.0 if term in text else 0.0 for term in terms if len(term) > 3)


def _symbol_score(symbol: str, terms: list[str]) -> float:
    symbol_terms = set(_identifier_parts(symbol))
    score = 0.0
    for term in terms:
        if term == symbol.lower():
            score += 10.0
        elif term in symbol_terms:
            score += 5.0
        elif len(term) > 3 and term in symbol.lower():
            score += 3.0
    return score


def _block_anchor_score(symbol: str, block_text: str) -> float:
    score = 0.0
    symbol_lower = symbol.lower()
    if any(part in symbol_lower for part in ("reward", "feature", "train", "blend", "store", "retrieve", "persist", "schema", "cache")):
        score += 2.0
    if any(anchor in block_text for anchor in ("create table", "json.loads", "reward_total", "threshold", "default", "ranked_files")):
        score += 2.0
    return score


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(elt.id for elt in target.elts if isinstance(elt, ast.Name))
    return names


def _constantish(text: str) -> bool:
    return bool(
        re.search(
            r"\b([A-Z][A-Z0-9_]{2,}|default|defaults|threshold|timeout|ttl|limit|weight|weights|ratio|budget|target|schema|contract)\b",
            text,
            re.IGNORECASE,
        )
        and "=" in text
    )


def _role_for_text(path: str, text: str) -> EvidenceSpanRole:
    lowered = text.lower()
    if path.startswith(("tests/", "test/")) or "/tests/" in path or "assert " in lowered:
        return "test_or_example"
    if re.search(r"\b(create table|create index|using fts5|alter table|schema|select\b.+\bfrom|insert into)\b", lowered):
        return "schema_or_storage"
    if re.search(r"\b(default|threshold|timeout|ttl|limit|weight|weights|ratio|budget|target|contract)\b", lowered) and "=" in text:
        return "constant_or_default"
    if re.search(r"\b(call|caller|consumer|downstream|insert|select)\b|retrieve|persist|compute_|load_|save_|train|blend|parse", lowered):
        return "caller_or_downstream"
    if re.search(r"\b(class|def|async def)\s+[A-Za-z_][A-Za-z0-9_]*", text):
        return "direct"
    return "supporting"


def _role_for_block(path: str, symbol: str, block_text: str) -> EvidenceSpanRole:
    semantic = _role_for_text(path, block_text)
    if semantic in {"schema_or_storage", "test_or_example"}:
        return semantic
    lowered_symbol = symbol.lower()
    if re.search(r"\b(downstream|consumer|caller|train|target|blend|parse|json\.loads|retrieve|persist|insert|select)\b", block_text):
        return "caller_or_downstream"
    if any(part in lowered_symbol for part in ("train", "target", "blend", "parse", "retrieve", "persist")):
        return "caller_or_downstream"
    if semantic == "constant_or_default":
        return semantic
    return "direct" if block_text.strip() else "supporting"


def _role_priority(role: EvidenceSpanRole) -> int:
    return {
        "direct": 70,
        "constant_or_default": 60,
        "schema_or_storage": 55,
        "caller_or_downstream": 50,
        "test_or_example": 45,
        "supporting": 25,
        "provenance": 10,
    }[role]


def _focused_block_window(lines: list[str], start: int, end: int, terms: list[str], *, max_lines: int) -> tuple[int, int]:
    if end - start + 1 <= max_lines:
        return start, end
    best_line = start
    best_score = -1.0
    for index in range(start, end + 1):
        score = _node_line_score(lines[index - 1], terms, "")
        if score > best_score:
            best_line = index
            best_score = score
    half = max_lines // 2
    window_start = max(start, best_line - half)
    window_end = min(end, window_start + max_lines - 1)
    window_start = max(start, window_end - max_lines + 1)
    return window_start, window_end


def _merge_candidate_windows(
    candidates: list[tuple[int, int, EvidenceSpanRole, str, float, str]],
) -> list[tuple[int, int, EvidenceSpanRole, str, float, str]]:
    ordered = sorted(candidates, key=lambda row: (row[0], row[1], -row[4]))
    merged: list[tuple[int, int, EvidenceSpanRole, str, float, str]] = []
    for candidate in ordered:
        start, end, role, symbol, score, reason = candidate
        if not merged or start > merged[-1][1] + 1:
            merged.append(candidate)
            continue
        prev_start, prev_end, prev_role, prev_symbol, prev_score, prev_reason = merged[-1]
        best_role = role if _merge_role_priority(role) > _merge_role_priority(prev_role) else prev_role
        best_symbol = symbol or prev_symbol
        merged[-1] = (
            prev_start,
            max(prev_end, end),
            best_role,
            best_symbol,
            max(prev_score, score),
            prev_reason if prev_score >= score else reason,
        )
    return merged


def _merge_role_priority(role: EvidenceSpanRole) -> int:
    return {
        "schema_or_storage": 80,
        "constant_or_default": 75,
        "caller_or_downstream": 70,
        "test_or_example": 65,
        "direct": 55,
        "supporting": 25,
        "provenance": 10,
    }[role]


def _diverse_spans(spans: list[EvidenceSpan], *, max_spans_per_file: int) -> list[EvidenceSpan]:
    kept: list[EvidenceSpan] = []
    seen_roles: set[str] = set()
    for span in spans:
        if span.role in seen_roles and len([item for item in kept if item.role == span.role]) >= 3:
            continue
        kept.append(span)
        seen_roles.add(span.role)
        if len(kept) >= max_spans_per_file:
            break
    kept.sort(key=lambda span: (_role_priority(span.role), span.score), reverse=True)
    return kept


def _numbered_excerpt(lines: list[str], start: int, end: int) -> str:
    width = len(str(end))
    return "\n".join(f"L{line_no:0{width}d}: {lines[line_no - 1]}" for line_no in range(start, end + 1))


def _symbol_from_line(line: str) -> str:
    match = re.search(r"\b(?:class|def|async def)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
    if match:
        return match.group(1)
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
    if match:
        return match.group(1)
    return ""


def _identifier_parts(identifier: str) -> list[str]:
    pieces = re.split(r"[^A-Za-z0-9]+", identifier)
    parts: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        parts.append(piece.lower())
        parts.extend(part.lower() for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", piece))
    return [part for part in parts if len(part) > 2]


def _chunk_for_spans(path: str, spans: list[EvidenceSpan]) -> str:
    parts = [f"### {path}"]
    for span in spans:
        parts.append(_span_entry(span))
    return "\n".join(parts).rstrip() + "\n"


def _span_entry(span: EvidenceSpan) -> str:
    symbol = f" symbol={span.symbol}" if span.symbol else ""
    protected = " protected" if span.protected else ""
    return f"@@ lines {span.start_line}-{span.end_line} role={span.role}{symbol}{protected}\n{span.excerpt}"


def _compress_context_with_spans(
    artefacts: list[Artefact],
    max_tokens: int,
    query: str,
    score_by_key: dict[str, float] | None,
) -> tuple[str, dict[str, int], int, set[str]]:
    token_map: dict[str, int] = {}
    kept_spans_by_key: dict[str, list[EvidenceSpan]] = {artefact.key: [] for artefact in artefacts}
    spans_by_key: dict[str, list[EvidenceSpan]] = {}
    ordered_keys = [artefact.key for artefact in artefacts]
    if score_by_key is not None:
        ordered_keys.sort(key=lambda key: score_by_key.get(key, 0.0), reverse=True)
    artefacts_by_key = {artefact.key: artefact for artefact in artefacts}
    for artefact in artefacts:
        spans = extract_evidence_spans(artefact, query, base_score=(score_by_key or {}).get(artefact.key, 0.0))
        if not spans:
            fallback = _chunk_for_artefact(
                artefact,
                max_chunk_tokens=max(96, min(max_tokens, max_tokens // max(1, min(3, len(artefacts) or 1)))),
            )
            pseudo_span = EvidenceSpan(
                path=artefact.source_path,
                start_line=1,
                end_line=max(1, len((artefact.content or "").splitlines())),
                role="supporting",
                symbol="",
                excerpt=fallback,
                score=(score_by_key or {}).get(artefact.key, 0.0),
                protected=False,
                reason="fallback compacted artefact",
            )
            spans = [pseudo_span] if fallback.strip() else []
        spans_by_key[artefact.key] = spans

    role_limits = _role_token_limits(max_tokens)
    role_used = {role: 0 for role in role_limits}
    role_kept = {role: 0 for role in role_limits}
    used = 0
    seen_signatures: set[str] = set()
    all_spans: list[tuple[str, EvidenceSpan]] = []
    for key in ordered_keys:
        for span in spans_by_key.get(key, []):
            all_spans.append((key, span))
    all_spans.sort(
        key=lambda row: (
            row[1].protected,
            _role_priority(row[1].role),
            row[1].score,
            (score_by_key or {}).get(row[0], 0.0),
        ),
        reverse=True,
    )

    for key, span in all_spans:
        header_tokens = 0 if kept_spans_by_key[key] else count_tokens(f"### {artefacts_by_key[key].source_path}\n")
        span_tokens = header_tokens + count_tokens(_span_entry(span))
        signature = _span_signature(span)
        if signature in seen_signatures:
            continue
        role_limit = role_limits.get(span.role, role_limits["supporting"])
        if role_used.get(span.role, 0) + span_tokens > role_limit:
            if not span.protected or role_kept.get(span.role, 0) >= 3:
                continue
        if used + span_tokens > max_tokens:
            if span.protected:
                trimmed = _trim_span(span, max(48, max_tokens - used - header_tokens))
                trimmed_tokens = header_tokens + count_tokens(_span_entry(trimmed))
                if used + trimmed_tokens > max_tokens:
                    continue
                span = trimmed
                span_tokens = trimmed_tokens
            else:
                continue
        kept_spans_by_key[key].append(span)
        seen_signatures.add(signature)
        role_used[span.role] = role_used.get(span.role, 0) + span_tokens
        role_kept[span.role] = role_kept.get(span.role, 0) + 1
        used += span_tokens

    kept_keys = {key for key, spans in kept_spans_by_key.items() if spans}
    if not kept_keys and ordered_keys and max_tokens > 0:
        key = ordered_keys[0]
        chunk = _trim_chunk_to_budget(_chunk_for_artefact(artefacts_by_key[key], max_chunk_tokens=max_tokens), max_tokens)
        if chunk:
            token_map[key] = count_tokens(chunk)
            return chunk, token_map, token_map[key], {key}

    chunks: list[str] = []
    for key in ordered_keys:
        spans = kept_spans_by_key[key]
        if not spans:
            token_map[key] = count_tokens(_chunk_for_artefact(artefacts_by_key[key], max_chunk_tokens=256))
            continue
        spans.sort(key=lambda span: (_role_priority(span.role), span.score), reverse=True)
        chunk = _chunk_for_spans(artefacts_by_key[key].source_path, spans)
        token_map[key] = count_tokens(chunk)
        chunks.append(chunk)
    used = sum(token_map[key] for key in kept_keys)
    return "\n".join(chunks).strip(), token_map, used, kept_keys


def _role_token_limits(max_tokens: int) -> dict[EvidenceSpanRole, int]:
    minimum = min(max_tokens, 96)
    return {
        "direct": max(minimum, int(max_tokens * 0.48)),
        "constant_or_default": max(minimum // 2, int(max_tokens * 0.14)),
        "schema_or_storage": max(minimum // 2, int(max_tokens * 0.12)),
        "caller_or_downstream": max(minimum // 2, int(max_tokens * 0.12)),
        "test_or_example": max(minimum // 2, int(max_tokens * 0.08)),
        "supporting": max(minimum // 2, int(max_tokens * 0.05)),
        "provenance": max(24, int(max_tokens * 0.01)),
    }


def _span_signature(span: EvidenceSpan) -> str:
    normalized = re.sub(r"\s+", " ", span.excerpt.lower()).strip()
    return f"{span.role}:{normalized[:220]}"


def _trim_span(span: EvidenceSpan, budget: int) -> EvidenceSpan:
    excerpt = _trim_chunk_to_budget(span.excerpt, budget)
    return EvidenceSpan(
        path=span.path,
        start_line=span.start_line,
        end_line=span.end_line,
        role=span.role,
        symbol=span.symbol,
        excerpt=excerpt,
        score=span.score,
        protected=span.protected,
        reason=f"{span.reason}; trimmed to fit transport budget",
    )


def compress_context(
    artefacts: list[Artefact],
    max_tokens: int,
    score_by_key: dict[str, float] | None = None,
    query: str | None = None,
) -> tuple[str, dict[str, int], int, set[str]]:
    if query:
        return _compress_context_with_spans(artefacts, max_tokens, query, score_by_key)

    token_map: dict[str, int] = {}
    chunk_map: dict[str, str] = {}
    ordered_keys: list[str] = []
    kept_keys: set[str] = set()
    used = 0

    per_chunk_cap = max(512, min(max_tokens, max_tokens // 3 if max_tokens >= 4096 else max_tokens))
    for artefact in artefacts:
        chunk = _chunk_for_artefact(artefact, max_chunk_tokens=per_chunk_cap)
        chunk_tokens = count_tokens(chunk)
        token_map[artefact.key] = chunk_tokens
        chunk_map[artefact.key] = chunk
        ordered_keys.append(artefact.key)

    if score_by_key is not None:
        ordered_keys.sort(key=lambda key: score_by_key.get(key, 0.0), reverse=True)

    # Greedy knapsack approximation: pack highest-score artefacts first while
    # still trying lower-ranked/smaller chunks that fit remaining budget.
    for index, key in enumerate(ordered_keys):
        chunk_tokens = token_map[key]
        if used + chunk_tokens > max_tokens:
            if index == 0 and not kept_keys:
                trimmed = _trim_chunk_to_budget(chunk_map[key], max_tokens)
                if trimmed:
                    chunk_map[key] = trimmed
                    token_map[key] = count_tokens(trimmed)
                    kept_keys.add(key)
                    used += token_map[key]
            continue
        kept_keys.add(key)
        used += chunk_tokens

    kept_chunks = [chunk_map[key] for key in ordered_keys if key in kept_keys]
    return "\n".join(kept_chunks), token_map, used, kept_keys
