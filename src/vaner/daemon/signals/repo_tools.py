# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import fnmatch
from pathlib import Path

from vaner.paths import resolve_repo_path


def _list_files_sync(repo_root: Path, path: str = ".") -> str:
    target = resolve_repo_path(repo_root, path)
    if not target.exists():
        return f"Path does not exist: {path}"
    if not target.is_dir():
        return f"Path is not a directory: {path}"
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    return "\n".join(entries) if entries else f"No files found in: {path}"


def _read_file_sync(repo_root: Path, path: str, max_chars: int = 12000) -> str:
    target = resolve_repo_path(repo_root, path)
    if not target.exists():
        return f"File does not exist: {path}"
    if not target.is_file():
        return f"Path is not a file: {path}"
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[truncated]"
    return text


def _find_files_sync(repo_root: Path, pattern: str, path: str = ".") -> str:
    target = resolve_repo_path(repo_root, path)
    if not target.exists():
        return f"Path does not exist: {path}"
    matches: list[str] = []
    for file_path in target.rglob("*"):
        rel = str(file_path.relative_to(repo_root))
        if fnmatch.fnmatch(file_path.name, pattern) or fnmatch.fnmatch(rel, pattern):
            matches.append(rel + ("/" if file_path.is_dir() else ""))
    matches = sorted(set(matches))
    return "\n".join(matches[:200]) if matches else f"No files matched pattern '{pattern}' under {path}"


def _grep_text_sync(
    repo_root: Path,
    query: str,
    path: str = ".",
    file_pattern: str = "*",
    max_results: int = 50,
) -> str:
    target = resolve_repo_path(repo_root, path)
    results: list[str] = []
    for file_path in target.rglob("*"):
        if not file_path.is_file():
            continue
        rel = str(file_path.relative_to(repo_root))
        if not (fnmatch.fnmatch(file_path.name, file_pattern) or fnmatch.fnmatch(rel, file_pattern)):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if query.lower() in line.lower():
                results.append(f"{rel}:{lineno}: {line.strip()}")
                if len(results) >= max_results:
                    return "\n".join(results)
    return "\n".join(results) if results else f"No matches for '{query}'"


async def list_files(repo_root: Path, path: str = ".") -> str:
    return await asyncio.to_thread(_list_files_sync, repo_root, path)


async def read_file(repo_root: Path, path: str) -> str:
    return await asyncio.to_thread(_read_file_sync, repo_root, path)


async def find_files(repo_root: Path, pattern: str, path: str = ".") -> str:
    return await asyncio.to_thread(_find_files_sync, repo_root, pattern, path)


async def grep_text(
    repo_root: Path,
    query: str,
    path: str = ".",
    file_pattern: str = "*",
    max_results: int = 50,
) -> str:
    return await asyncio.to_thread(_grep_text_sync, repo_root, query, path, file_pattern, max_results)
