"""LangGraph analyzer graph — walks the repo, writes file/dir summaries to .vaner/cache/.

Graph flow:
    __start__
        → discover_targets
        → filter_stale
        → generate_file_summaries  (or END if nothing stale)
        → generate_dir_summaries
        → update_repo_index
        → END
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from vaner_tools.artefact_store import (
    Artefact,
    is_stale,
    list_artefacts,
    read_artefact,
    write_artefact,
)
from vaner_tools.paths import CACHE_DIR, REPO_ROOT, resolve_repo_path

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

model = ChatOllama(model="devstral", temperature=0)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

FILE_SUMMARY_SYSTEM = (
    "You are generating a file summary for a context-retrieval system.\n"
    "The summary will be keyword-matched against developer questions, so include\n"
    "concrete nouns: function names, class names, key concepts, and technical terms.\n\n"
    "Given the file path and contents, write 3-5 sentences covering:\n"
    "- What this file does and its primary responsibility\n"
    "- Key exports: class names, function names, constants (name them explicitly)\n"
    "- Dependencies or notable imports\n"
    "- How it fits into the project (if inferable)\n"
    "Be factual and specific. Do not invent. If trivial (e.g. empty __init__.py), say so in one sentence."
)

DIR_SUMMARY_SYSTEM = (
    "You are generating a directory summary for a context-retrieval system.\n"
    "Given summaries of files within the directory, write 3-5 sentences covering:\n"
    "- What the directory contains as a whole and its purpose\n"
    "- The main entry points or most important modules (name them explicitly)\n"
    "- Key classes, functions, or concepts present (name them)\n"
    "- How this directory fits into the broader project\n"
    "Be factual and specific. Do not invent."
)

# Directories to skip during discovery
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".vaner", ".ruff_cache", ".mypy_cache", ".pytest_cache", "dist", "build"}

# File suffixes and name patterns to skip (non-code noise)
SKIP_SUFFIXES = {".pyc", ".pyo", ".pyd", ".so", ".egg", ".whl"}
SKIP_NAMES = {"RECORD", "WHEEL", "METADATA", "top_level.txt", "dependency_links.txt", "entry_points.txt", "direct_url.json", "INSTALLER", "LICENSE.txt"}

# Max file size to summarize (50 KB)
MAX_FILE_SIZE = 50 * 1024

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class AnalyzerState:
    target_path: str = "."
    force_refresh: bool = False
    artefacts_written: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Node: discover_targets
# ---------------------------------------------------------------------------


def _discover_sync(target_path: str) -> list[str]:
    """Walk target_path and return repo-relative path strings for eligible files."""
    try:
        root = resolve_repo_path(target_path)
    except ValueError:
        return []

    if root.is_file():
        # Single file — check eligibility
        try:
            if root.stat().st_size <= MAX_FILE_SIZE:
                root.read_text(encoding="utf-8")
                return [str(root.relative_to(REPO_ROOT))]
        except Exception:
            pass
        return []

    collected: list[str] = []
    for p in root.rglob("*"):
        # Skip directories matching SKIP_DIRS anywhere in path
        parts = p.parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        # Skip .egg-info directories
        if any(part.endswith(".egg-info") for part in parts):
            continue
        if not p.is_file():
            continue
        # Skip non-code suffixes and known noise filenames
        if p.suffix in SKIP_SUFFIXES or p.name in SKIP_NAMES:
            continue
        if p.stat().st_size > MAX_FILE_SIZE:
            continue
        try:
            p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, Exception):
            continue
        collected.append(str(p.relative_to(REPO_ROOT)))

    return sorted(collected)


async def discover_targets(state: AnalyzerState) -> dict[str, Any]:
    targets = await asyncio.to_thread(_discover_sync, state.target_path)
    return {"targets": targets}


# ---------------------------------------------------------------------------
# Node: filter_stale
# ---------------------------------------------------------------------------


async def filter_stale(state: AnalyzerState) -> dict[str, Any]:
    if state.force_refresh:
        return {"targets": state.targets}

    stale: list[str] = []
    for rel_path in state.targets:
        existing = read_artefact("file_summary", rel_path)
        if existing is None or is_stale(existing):
            stale.append(rel_path)

    return {"targets": stale}


def route_after_filter(state: AnalyzerState) -> str:
    return "generate_file_summaries" if state.targets else END


# ---------------------------------------------------------------------------
# Node: generate_file_summaries
# ---------------------------------------------------------------------------


async def _summarize_file(rel_path: str) -> tuple[str, str | None]:
    """Return (rel_path, error_or_none) after writing the file_summary artefact."""
    abs_path = REPO_ROOT / rel_path
    try:
        content = abs_path.read_text(encoding="utf-8")
        mtime = abs_path.stat().st_mtime
    except Exception as e:
        return rel_path, f"read error: {e}"

    prompt = (
        f"{FILE_SUMMARY_SYSTEM}\n\n"
        f"File path: {rel_path}\n\n"
        f"File contents:\n{content}"
    )
    try:
        result = await model.ainvoke(prompt)
        summary = result.content
        if isinstance(summary, list):
            summary = "\n".join(
                p.get("text", str(p)) if isinstance(p, dict) else str(p) for p in summary
            )
        summary = str(summary).strip()
    except Exception as e:
        return rel_path, f"LLM error: {e}"

    artefact = Artefact(
        key=f"file_summary:{rel_path}",
        kind="file_summary",
        source_path=rel_path,
        source_mtime=mtime,
        generated_at=time.time(),
        model="devstral",
        content=summary,
        metadata={},
    )
    try:
        write_artefact(artefact)
    except Exception as e:
        return rel_path, f"write error: {e}"

    return rel_path, None


async def generate_file_summaries(state: AnalyzerState) -> dict[str, Any]:
    batch_size = 5
    written: list[str] = list(state.artefacts_written)
    errors: list[str] = list(state.errors)

    targets = list(state.targets)
    for i in range(0, len(targets), batch_size):
        batch = targets[i : i + batch_size]
        results = await asyncio.gather(*[_summarize_file(p) for p in batch])
        for rel_path, err in results:
            if err:
                errors.append(f"{rel_path}: {err}")
            else:
                written.append(f"file_summary:{rel_path}")

    return {"artefacts_written": written, "errors": errors}


# ---------------------------------------------------------------------------
# Node: generate_dir_summaries
# ---------------------------------------------------------------------------


async def generate_dir_summaries(state: AnalyzerState) -> dict[str, Any]:
    written: list[str] = list(state.artefacts_written)
    errors: list[str] = list(state.errors)

    # Collect unique parent directories of processed files
    dirs: set[str] = set()
    for key in state.artefacts_written:
        if key.startswith("file_summary:"):
            rel_path = key[len("file_summary:"):]
            parent = str(Path(rel_path).parent)
            if parent != ".":
                dirs.add(parent)

    for dir_path in sorted(dirs):
        # Gather file_summary artefacts under this dir
        child_summaries: list[str] = []
        for art in list_artefacts("file_summary"):
            if art.source_path.startswith(dir_path + "/") or Path(art.source_path).parent == Path(dir_path):
                child_summaries.append(f"### {art.source_path}\n{art.content}")

        if not child_summaries:
            continue

        prompt = (
            f"{DIR_SUMMARY_SYSTEM}\n\n"
            f"Directory: {dir_path}\n\n"
            + "\n\n".join(child_summaries)
        )
        try:
            result = await model.ainvoke(prompt)
            summary = result.content
            if isinstance(summary, list):
                summary = "\n".join(
                    p.get("text", str(p)) if isinstance(p, dict) else str(p) for p in summary
                )
            summary = str(summary).strip()
        except Exception as e:
            errors.append(f"{dir_path} dir summary: {e}")
            continue

        # Use the dir path itself as source_path for the artefact
        artefact = Artefact(
            key=f"dir_summary:{dir_path}",
            kind="dir_summary",
            source_path=dir_path,
            source_mtime=time.time(),
            generated_at=time.time(),
            model="devstral",
            content=summary,
            metadata={},
        )
        try:
            write_artefact(artefact)
            written.append(f"dir_summary:{dir_path}")
        except Exception as e:
            errors.append(f"{dir_path}: write error: {e}")

    return {"artefacts_written": written, "errors": errors}


# ---------------------------------------------------------------------------
# Node: update_repo_index
# ---------------------------------------------------------------------------


async def update_repo_index(state: AnalyzerState) -> dict[str, Any]:
    errors: list[str] = list(state.errors)
    written: list[str] = list(state.artefacts_written)

    all_file_summaries = list_artefacts("file_summary")
    files_index: dict[str, dict] = {}
    for art in all_file_summaries:
        files_index[art.source_path] = {
            "kind": art.kind,
            "summary": art.content,
            "mtime": art.source_mtime,
        }

    index_content = json.dumps(
        {"generated_at": time.time(), "files": files_index},
        indent=2,
    )

    # Write to the special path .vaner/cache/repo_index/root.json
    index_path = CACHE_DIR / "repo_index" / "root.json"
    try:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(index_content, encoding="utf-8")
        written.append("repo_index:root")
    except Exception as e:
        errors.append(f"repo_index write error: {e}")

    return {"artefacts_written": written, "errors": errors}


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

graph = (
    StateGraph(AnalyzerState)
    .add_node("discover_targets", discover_targets)
    .add_node("filter_stale", filter_stale)
    .add_node("generate_file_summaries", generate_file_summaries)
    .add_node("generate_dir_summaries", generate_dir_summaries)
    .add_node("update_repo_index", update_repo_index)
    .add_edge("__start__", "discover_targets")
    .add_edge("discover_targets", "filter_stale")
    .add_conditional_edges(
        "filter_stale",
        route_after_filter,
        {
            "generate_file_summaries": "generate_file_summaries",
            END: END,
        },
    )
    .add_edge("generate_file_summaries", "generate_dir_summaries")
    .add_edge("generate_dir_summaries", "update_repo_index")
    .add_edge("update_repo_index", END)
    .compile(name="Vaner Repo Analyzer")
)
