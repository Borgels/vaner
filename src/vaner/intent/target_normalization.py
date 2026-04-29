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
    "add",
    "build",
    "create",
    "implement",
    "implementation",
    "concrete",
    "component",
    "cluster",
}

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[./\-][A-Za-z_][A-Za-z0-9_]*)*")
_ACRONYM_BOUNDARY_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")


def normalize_component(value: str) -> str:
    text = value.strip().replace("\\", "/")
    if not text:
        return ""
    if text.startswith("/") or re.match(r"^[A-Za-z]:/", text):
        text = PurePosixPath(text[2:] if re.match(r"^[A-Za-z]:/", text) else text).name
    text = text.replace("artefact", "artifact").replace("Artefact", "Artifact")
    text = _ACRONYM_BOUNDARY_RE.sub(r"\1 \2", text)
    text = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", text)
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", text.lower()) if part]
    return "".join(_singularize(part) for part in parts)


def component_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        stripped = raw.strip("`'\".,;:()[]{}")
        candidates = [stripped.rsplit("/", 1)[-1]]
        if "." in candidates[0]:
            candidates.extend(part for part in candidates[0].split(".") if part)
        for candidate in candidates:
            normalized = normalize_component(candidate)
            if not normalized or len(normalized) < 3 or normalized in _STOPWORDS:
                continue
            if _is_concrete(candidate, normalized) and normalized not in seen:
                seen.add(normalized)
                terms.append(normalized)
    return tuple(terms)


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
    return "/" in raw or "." in raw or "_" in raw or "-" in raw or any(ch.isupper() for ch in raw[1:]) or len(normalized) >= 5
