# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path

import httpx

from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.config import VanerConfig
from vaner.policy.privacy import redact_text

logger = logging.getLogger(__name__)

FILE_SUMMARY_PROMPT = """You are generating a precise implementation reference for a developer context system.
A model will later use this summary to answer exact questions about this file.
Your summary MUST enable correct answers — not just plausible ones.

Include ALL of the following that are present in the file:

1. CLASSES: name, base class, key attributes with types/defaults
2. FUNCTIONS/METHODS: name, parameter names and types, return type, one-line behavior description
3. KEY CONSTANTS: exact name and exact value
4. IMPORTANT CONDITIONALS: exact conditions and what branches they control
5. EXCEPTION TYPES: which exceptions are caught or raised and why
6. FORMULAS/ALGORITHMS: exact expressions
7. LIMITS/TRUNCATION/CACHING: exact caps, slices, TTLs, or bounded windows
8. WHAT THIS FILE DOES NOT DO: avoid conflating it with files that call it

Do NOT paraphrase implementation details. Use exact names from the code.
Do NOT summarize in prose where specifics exist.
Maximum {max_tokens} tokens.

File: {path}
---
{content}
---
Implementation reference:"""

DIFF_SUMMARY_PROMPT = """Summarize the following git diff for a developer context system.
State clearly what changed, which modules/functions were affected, and likely intent.
Call out exact limits/guards/conditionals when visible.
Be concise.

{diff}
---
Summary:"""


def _extract_python_shapes(text: str) -> tuple[list[str], list[str]]:
    """Extract class names, top-level functions, and class method names from Python sources."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], []

    classes: list[str] = []
    functions: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
            # Include method names so artefact content matches method-specific queries
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not item.name.startswith("_"):
                        functions.append(item.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [arg.arg for arg in node.args.args]
            functions.append(f"{node.name}({', '.join(args)})")

    return classes[:8], list(dict.fromkeys(functions))[:20]


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


def _summarize_text(
    text: str,
    source_path: Path,
    max_lines: int = 8,
    full_text: str | None = None,
) -> str:
    """Summarise a (potentially truncated) source file.

    ``full_text``, when provided, is used for AST-level shape extraction so
    that method names beyond the ``max_file_chars`` truncation boundary are
    still captured.  The ``text`` argument is used for constants, limits, and
    the snippet section.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "Empty or whitespace-only file."

    ast_source = full_text if full_text is not None else text

    sections: list[str] = []
    constants = _extract_constants(text)
    limits = _extract_limits(text)

    if source_path.suffix == ".py":
        classes, functions = _extract_python_shapes(ast_source)
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


def _extract_message_text(payload: dict) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
    return ""


async def _llm_summarize(text: str, prompt_template: str, config: VanerConfig, source_label: str) -> str | None:
    model = config.generation.generation_model or config.backend.model
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_template}],
        "temperature": 0,
        "max_tokens": config.generation.summary_max_tokens,
    }
    headers = {"Content-Type": "application/json"}
    api_key = ""
    if config.backend.api_key_env:
        import os

        api_key = os.getenv(config.backend.api_key_env, "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        timeout_seconds = max(1.0, float(getattr(config.generation, "llm_timeout_seconds", 30.0)))
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(f"{config.backend.base_url.rstrip('/')}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
        content = _extract_message_text(response.json()).strip()
        return content or None
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("LLM summary failed for %s: %s", source_label, exc)
        return None


async def agenerate_file_summary(
    source_path: Path,
    repo_root: Path,
    model_name: str = "heuristic-local",
    redact_patterns: list[str] | None = None,
    config: VanerConfig | None = None,
) -> Artefact:
    raw_text = source_path.read_text(encoding="utf-8", errors="ignore")
    sanitized_text = redact_text(raw_text, redact_patterns or [])
    rel_path = str(source_path.relative_to(repo_root))
    max_chars = config.generation.max_file_chars if config is not None else 8000
    bounded_text = sanitized_text[:max_chars]
    # Pass full text so that AST shape extraction covers the whole file
    heuristic_content = _summarize_text(bounded_text, source_path, full_text=sanitized_text)
    content = heuristic_content
    summary_mode = "heuristic"

    if config is not None and config.generation.use_llm and model_name != "heuristic-local":
        llm_prompt = FILE_SUMMARY_PROMPT.format(
            path=rel_path,
            content=bounded_text,
            max_tokens=config.generation.summary_max_tokens,
        )
        llm_content = await _llm_summarize(bounded_text, llm_prompt, config, rel_path)
        if llm_content:
            content = llm_content
            summary_mode = "llm"

    return _build_artefact(
        key=f"{ArtefactKind.FILE_SUMMARY.value}:{rel_path}",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path=rel_path,
        source_mtime=source_path.stat().st_mtime,
        model_name=model_name,
        content=content,
        metadata={
            "hash": hashlib.sha256(sanitized_text.encode("utf-8")).hexdigest()[:16],
            "summary_mode": summary_mode,
        },
    )


def generate_file_summary(
    source_path: Path,
    repo_root: Path,
    model_name: str = "heuristic-local",
    redact_patterns: list[str] | None = None,
    config: VanerConfig | None = None,
) -> Artefact:
    return asyncio.run(
        agenerate_file_summary(
            source_path,
            repo_root,
            model_name=model_name,
            redact_patterns=redact_patterns,
            config=config,
        )
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


async def agenerate_diff_summary(
    repo_root: Path,
    relative_path: str,
    diff_text: str,
    model_name: str = "heuristic-local",
    redact_patterns: list[str] | None = None,
    config: VanerConfig | None = None,
) -> Artefact:
    redacted_diff = redact_text(diff_text, redact_patterns or [])
    compact = _summarize_text(redacted_diff, repo_root / relative_path, max_lines=20)
    if config is not None and config.generation.use_llm and model_name != "heuristic-local":
        llm_prompt = DIFF_SUMMARY_PROMPT.format(diff=redacted_diff[: config.generation.max_file_chars])
        llm_content = await _llm_summarize(redacted_diff, llm_prompt, config, relative_path)
        if llm_content:
            compact = llm_content
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


def generate_diff_summary(
    repo_root: Path,
    relative_path: str,
    diff_text: str,
    model_name: str = "heuristic-local",
    redact_patterns: list[str] | None = None,
    config: VanerConfig | None = None,
) -> Artefact:
    return asyncio.run(
        agenerate_diff_summary(
            repo_root,
            relative_path,
            diff_text,
            model_name=model_name,
            redact_patterns=redact_patterns,
            config=config,
        )
    )


def generate_artefact(source_path: Path, repo_root: Path, model_name: str = "heuristic-local") -> Artefact:
    """Backward-compatible alias for file-summary generation."""
    return generate_file_summary(source_path, repo_root, model_name=model_name)
