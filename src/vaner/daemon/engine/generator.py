# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import hashlib
import re
import time
from pathlib import Path

from vaner.models.artefact import Artefact, ArtefactKind
from vaner.policy.privacy import redact_text


def _extract_python_shapes(text: str) -> tuple[list[str], list[str]]:
    """Extract class and function signatures from python sources."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], []

    classes: list[str] = []
    functions: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.FunctionDef):
            args = [arg.arg for arg in node.args.args]
            functions.append(f"{node.name}({', '.join(args)})")

    return classes[:8], functions[:12]


def _extract_limits(text: str) -> list[str]:
    limit_patterns = [
        r"\b(max|min|limit|ttl|timeout|window|top_n|slice|budget)\w*\b.*",
        r".*\[\s*:\s*\d+\s*\].*",
    ]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    found: list[str] = []
    for line in lines:
        lowered = line.lower()
        if any(re.search(pattern, lowered) for pattern in limit_patterns):
            found.append(line[:180])
    return found[:10]


def _extract_constants(text: str) -> list[str]:
    constants: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if "=" not in stripped:
            continue
        lhs = stripped.split("=", 1)[0].strip()
        if lhs.isupper() and lhs.replace("_", "").isalnum():
            constants.append(stripped[:180])
    return constants[:12]


def _summarize_text(text: str, source_path: Path, max_lines: int = 8) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "Empty or whitespace-only file."

    sections: list[str] = []
    constants = _extract_constants(text)
    limits = _extract_limits(text)

    if source_path.suffix == ".py":
        classes, functions = _extract_python_shapes(text)
        if classes:
            sections.append("Classes: " + ", ".join(classes))
        if functions:
            sections.append("Functions: " + ", ".join(functions))

    if constants:
        sections.append("Constants: " + "; ".join(constants[:6]))
    if limits:
        sections.append("Limits: " + "; ".join(limits[:6]))

    sections.append("Snippet: " + " ".join(lines[:max_lines])[:900])
    return "\n".join(sections)[:1600]


def _build_artefact(
    *,
    key: str,
    kind: ArtefactKind,
    source_path: str,
    source_mtime: float,
    model_name: str,
    content: str,
    metadata: dict[str, str],
) -> Artefact:
    return Artefact(
        key=key,
        kind=kind,
        source_path=source_path,
        source_mtime=source_mtime,
        generated_at=time.time(),
        model=model_name,
        content=content,
        metadata=metadata,
    )


def generate_file_summary(
    source_path: Path,
    repo_root: Path,
    model_name: str = "heuristic-local",
    redact_patterns: list[str] | None = None,
) -> Artefact:
    raw_text = source_path.read_text(encoding="utf-8", errors="ignore")
    sanitized_text = redact_text(raw_text, redact_patterns or [])
    rel_path = str(source_path.relative_to(repo_root))
    content = _summarize_text(sanitized_text, source_path)
    return _build_artefact(
        key=f"{ArtefactKind.FILE_SUMMARY.value}:{rel_path}",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path=rel_path,
        source_mtime=source_path.stat().st_mtime,
        model_name=model_name,
        content=content,
        metadata={"hash": hashlib.sha256(sanitized_text.encode("utf-8")).hexdigest()[:16]},
    )


def generate_dir_summary(
    directory: Path,
    repo_root: Path,
    child_summaries: list[Artefact],
    model_name: str = "heuristic-local",
) -> Artefact:
    rel_dir = str(directory.relative_to(repo_root))
    aggregate = " ".join(summary.content for summary in child_summaries)
    content = _summarize_text(aggregate, directory, max_lines=12)
    return _build_artefact(
        key=f"{ArtefactKind.DIR_SUMMARY.value}:{rel_dir}",
        kind=ArtefactKind.DIR_SUMMARY,
        source_path=rel_dir,
        source_mtime=directory.stat().st_mtime,
        model_name=model_name,
        content=content,
        metadata={"children": str(len(child_summaries))},
    )


def generate_repo_index(repo_root: Path, files: list[Path], model_name: str = "heuristic-local") -> Artefact:
    rel_files = sorted(str(path.relative_to(repo_root)) for path in files[:200])
    sections = [
        "Repository index:",
        "Top-level entries:",
    ]
    top_level = sorted({entry.split("/", 1)[0] for entry in rel_files})
    sections.append(", ".join(top_level[:30]) if top_level else "none")
    sections.append("Representative files:")
    sections.extend(rel_files[:25])
    content = "\n".join(sections)
    return _build_artefact(
        key=f"{ArtefactKind.REPO_INDEX.value}:{repo_root.name}",
        kind=ArtefactKind.REPO_INDEX,
        source_path=".",
        source_mtime=repo_root.stat().st_mtime,
        model_name=model_name,
        content=content,
        metadata={"entries": str(len(rel_files))},
    )


def generate_diff_summary(
    repo_root: Path,
    relative_path: str,
    diff_text: str,
    model_name: str = "heuristic-local",
    redact_patterns: list[str] | None = None,
) -> Artefact:
    redacted_diff = redact_text(diff_text, redact_patterns or [])
    compact = _summarize_text(redacted_diff, repo_root / relative_path, max_lines=20)
    abs_path = (repo_root / relative_path).resolve()
    mtime = abs_path.stat().st_mtime if abs_path.exists() else time.time()
    return _build_artefact(
        key=f"{ArtefactKind.DIFF_SUMMARY.value}:{relative_path}",
        kind=ArtefactKind.DIFF_SUMMARY,
        source_path=relative_path,
        source_mtime=mtime,
        model_name=model_name,
        content=compact,
        metadata={"lines": str(len(redacted_diff.splitlines()))},
    )


def generate_artefact(source_path: Path, repo_root: Path, model_name: str = "heuristic-local") -> Artefact:
    """Backward-compatible alias for file-summary generation."""
    return generate_file_summary(source_path, repo_root, model_name=model_name)
