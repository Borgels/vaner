# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import PurePosixPath

_EXCLUDED_PARTS = {
    ".cache",
    ".coverage",
    ".eggs",
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".vaner",
    ".venv",
    ".vaner_data",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "target",
    "vendor",
    "vendors",
}

_EXCLUDED_SUFFIXES = {
    ".egg-info",
    ".dist-info",
}

_EXCLUDED_FILENAMES = {
    ".coverage",
    "coverage.xml",
}

_EXCLUDED_EXTENSIONS = {
    ".avif",
    ".bin",
    ".bmp",
    ".db",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".lock",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".sqlite",
    ".sqlite3",
    ".svg",
    ".webp",
    ".zip",
}

_GENERATED_PART_PATTERNS = {
    "bindings",
    "generated",
    "gen",
}

_SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".go",
    ".h",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".py",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
}

_CONFIG_EXTENSIONS = {
    ".cfg",
    ".ini",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
}

_DOC_EXTENSIONS = {
    ".md",
    ".rst",
    ".txt",
}


def normalize_evidence_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def is_evidence_path_allowed(path: str) -> bool:
    """Return True when a relative path is useful source evidence."""
    normalized = normalize_evidence_path(path)
    if not normalized or normalized.startswith("../") or normalized.startswith("/"):
        return False
    parts = PurePosixPath(normalized).parts
    if any(part in _EXCLUDED_PARTS for part in parts):
        return False
    if "crates" in parts and "vaner-contract" in parts and "bindings" in parts:
        return False
    if any(part.endswith(tuple(_EXCLUDED_SUFFIXES)) for part in parts):
        return False
    pure = PurePosixPath(normalized)
    if pure.name in _EXCLUDED_FILENAMES:
        return False
    return pure.suffix.lower() not in _EXCLUDED_EXTENSIONS


def evidence_path_kind(path: str) -> str:
    """Classify an allowed relative path for prediction evidence roles."""
    normalized = normalize_evidence_path(path)
    pure = PurePosixPath(normalized)
    lowered_parts = tuple(part.lower() for part in pure.parts)
    suffix = pure.suffix.lower()
    name = pure.name.lower()
    if not is_evidence_path_allowed(normalized):
        return "rejected"
    if any(part in _GENERATED_PART_PATTERNS for part in lowered_parts):
        return "generated"
    if suffix in _SOURCE_EXTENSIONS:
        if (
            any(part in {"test", "tests", "__tests__"} for part in lowered_parts)
            or name.startswith("test_")
            or name.endswith("_test" + suffix)
        ):
            return "test"
        return "source"
    if suffix in _CONFIG_EXTENSIONS or name in {"dockerfile", "makefile"}:
        return "config"
    if suffix in _DOC_EXTENSIONS or "docs" in lowered_parts or name.startswith("readme"):
        return "docs"
    return "data"


def filter_evidence_paths(paths: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    seen: set[str] = set()
    filtered: list[str] = []
    for raw in paths:
        path = normalize_evidence_path(str(raw))
        if not is_evidence_path_allowed(path) or path in seen:
            continue
        seen.add(path)
        filtered.append(path)
    return filtered
