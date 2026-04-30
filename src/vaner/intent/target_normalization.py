# SPDX-License-Identifier: Apache-2.0
"""Deterministic target normalization for exact component matching."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

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

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[.\-][A-Za-z_][A-Za-z0-9_]*)*")


def normalize_component(value: str) -> str:
    """Normalize names across case styles, separators, plurals, and spelling.

    The return value is intentionally compact (``artefact-store`` and
    ``ArtefactStore`` both become ``artifactstore``) so it can be compared
    cheaply against symbols, path stems, and summary tokens.
    """

    text = value.strip().replace("\\", "/")
    if not text:
        return ""
    if text.startswith("/") or re.match(r"^[A-Za-z]:/", text):
        text = PurePosixPath(text[2:] if re.match(r"^[A-Za-z]:/", text) else text).name
    text = text.replace("artefact", "artifact").replace("Artefact", "Artifact")
    text = _split_case_boundaries(text)
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", text.lower()) if part]
    if len(parts) == 1:
        parts = [_singularize(parts[0])]
    else:
        parts = [_singularize(part) for part in parts]
    return "".join(parts)


def component_terms(text: str) -> tuple[str, ...]:
    """Return likely concrete component terms from prose, paths, and labels."""

    terms: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        stripped = raw.strip("`'\".,;:()[]{}")
        if not stripped:
            continue
        path_tail = stripped.rsplit("/", 1)[-1]
        candidates = [path_tail]
        if "." in path_tail:
            candidates.extend(part for part in path_tail.split(".") if part)
        if "-" in path_tail or "_" in path_tail:
            candidates.append(path_tail)
        for candidate in candidates:
            normalized = normalize_component(candidate)
            if not normalized or len(normalized) < 3 or normalized in _STOPWORDS:
                continue
            if _is_concrete(candidate, normalized) and normalized not in seen:
                seen.add(normalized)
                terms.append(normalized)
    return tuple(terms)


def _split_case_boundaries(text: str) -> str:
    out: list[str] = []
    for idx, char in enumerate(text):
        if idx > 0 and _needs_case_boundary(text, idx):
            out.append(" ")
        out.append(char)
    return "".join(out)


def _needs_case_boundary(text: str, idx: int) -> bool:
    prev = text[idx - 1]
    current = text[idx]
    next_char = text[idx + 1] if idx + 1 < len(text) else ""
    return (current.isupper() and (prev.islower() or prev.isdigit())) or (
        prev.isupper() and current.isupper() and bool(next_char) and next_char.islower()
    )


def path_component_terms(path: str) -> tuple[str, ...]:
    parts = [part for part in re.split(r"[/.\-_]+", path) if part]
    normalized = [normalize_component(part) for part in parts]
    combined: list[str] = []
    basename = PurePosixPath(path).stem
    if basename:
        combined.append(normalize_component(basename))
    if "-" in basename or "_" in basename:
        combined.append(normalize_component(basename.replace("-", "").replace("_", "")))
    return tuple(dict.fromkeys(term for term in [*normalized, *combined] if term and len(term) >= 3 and term not in _STOPWORDS))


def _singularize(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _is_concrete(raw: str, normalized: str) -> bool:
    if "/" in raw or "." in raw:
        return True
    if "_" in raw or "-" in raw:
        return True
    if any(ch.isupper() for ch in raw[1:]):
        return True
    return normalized not in _STOPWORDS and len(normalized) >= 5
