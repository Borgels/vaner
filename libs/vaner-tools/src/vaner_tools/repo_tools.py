"""Read-only filesystem tools for repo exploration.

Async-safe via asyncio.to_thread. All paths sandboxed to REPO_ROOT.
"""

from __future__ import annotations

import asyncio
import fnmatch

from .paths import REPO_ROOT, resolve_repo_path


# ---------------------------------------------------------------------------
# Internal sync implementations
# ---------------------------------------------------------------------------


def _list_files_sync(path: str = ".") -> str:
    target = resolve_repo_path(path)
    if not target.exists():
        return f"Path does not exist: {path}"
    if not target.is_dir():
        return f"Path is not a directory: {path}"

    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    if not entries:
        return f"No files found in: {path}"

    return "\n".join(entries)


def _read_file_sync(path: str) -> str:
    target = resolve_repo_path(path)
    if not target.exists():
        return f"File does not exist: {path}"
    if not target.is_file():
        return f"Path is not a file: {path}"

    text = target.read_text(encoding="utf-8")
    max_chars = 12000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[truncated]"
    return text


def _find_files_sync(pattern: str, path: str = ".") -> str:
    target = resolve_repo_path(path)
    if not target.exists():
        return f"Path does not exist: {path}"
    if not target.is_dir():
        return f"Path is not a directory: {path}"

    matches: list[str] = []
    for p in target.rglob("*"):
        rel = str(p.relative_to(REPO_ROOT))
        if fnmatch.fnmatch(p.name, pattern) or fnmatch.fnmatch(rel, pattern):
            matches.append(rel + ("/" if p.is_dir() else ""))

    matches = sorted(set(matches))
    if not matches:
        return f"No files matched pattern '{pattern}' under {path}"

    return "\n".join(matches[:200])


def _grep_text_sync(
    query: str,
    path: str = ".",
    file_pattern: str = "*",
    max_results: int = 50,
) -> str:
    target = resolve_repo_path(path)
    if not target.exists():
        return f"Path does not exist: {path}"
    if not target.is_dir():
        return f"Path is not a directory: {path}"

    results: list[str] = []

    for p in target.rglob("*"):
        if not p.is_file():
            continue

        rel = str(p.relative_to(REPO_ROOT))
        if not (fnmatch.fnmatch(p.name, file_pattern) or fnmatch.fnmatch(rel, file_pattern)):
            continue

        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            if query.lower() in line.lower():
                results.append(f"{rel}:{lineno}: {line.strip()}")
                if len(results) >= max_results:
                    return "\n".join(results)

    if not results:
        return f"No matches for '{query}' under {path} with file pattern '{file_pattern}'"

    return "\n".join(results)


# ---------------------------------------------------------------------------
# Async public API
# ---------------------------------------------------------------------------


async def list_files(path: str = ".") -> str:
    """List directory contents, sandboxed to repo root."""
    try:
        return await asyncio.to_thread(_list_files_sync, path)
    except Exception as e:
        return f"Error listing files in {path}: {e}"


async def read_file(path: str) -> str:
    """Read a text file, sandboxed to repo root. Truncates at 12 000 chars."""
    try:
        return await asyncio.to_thread(_read_file_sync, path)
    except UnicodeDecodeError:
        return f"File is not valid UTF-8 text: {path}"
    except Exception as e:
        return f"Error reading file {path}: {e}"


async def find_files(pattern: str, path: str = ".") -> str:
    """Glob-style file search under path, sandboxed to repo root."""
    try:
        return await asyncio.to_thread(_find_files_sync, pattern, path)
    except Exception as e:
        return f"Error finding files with pattern {pattern} in {path}: {e}"


async def grep_text(
    query: str,
    path: str = ".",
    file_pattern: str = "*",
    max_results: int = 50,
) -> str:
    """Search for query text in files under path."""
    try:
        return await asyncio.to_thread(
            _grep_text_sync, query, path, file_pattern, max_results
        )
    except Exception as e:
        return f"Error searching for '{query}' in {path}: {e}"
