"""Module-level summarizer — generates summaries for entire Python packages/dirs."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from langchain_ollama import ChatOllama
from vaner_tools.artefact_store import Artefact, list_artefacts, write_artefact

logger = logging.getLogger("vaner.module_summarizer")

MODULE_SUMMARY_PROMPT = """You are summarizing a Python module (directory) for a developer context system.

Below are summaries of the individual files in this module. Synthesize them into a single
cohesive module-level summary. Focus on:
- What is the overall purpose of this module?
- What are the key classes and functions it exposes?
- How do the files relate to each other?
- What are the main dependencies and integration points?

Be terse and keyword-rich. Max 200 words.

Module: {module_path}
File summaries:
{file_summaries}

Module summary:"""


def _collect_file_summaries(module_path: str, all_artefacts: list[Artefact]) -> str:
    """Collect file_summary artefacts that belong to this module path."""
    matching = [
        a for a in all_artefacts
        if a.kind == "file_summary" and a.source_path.startswith(module_path)
    ]
    if not matching:
        return ""
    parts = [f"### {a.source_path}\n{a.content}" for a in matching[:10]]
    return "\n\n".join(parts)


async def generate_module_summary(
    module_path: str,
    repo_root: Path,
    model_name: str = "qwen2.5-coder:32b",
) -> Artefact | None:
    """Generate a module-level summary from existing file_summary artefacts."""
    try:
        all_artefacts = list_artefacts(kind="file_summary")
        file_summaries = _collect_file_summaries(module_path, all_artefacts)
        if not file_summaries:
            logger.info("No file summaries found for module %s — skipping", module_path)
            return None

        model = ChatOllama(model=model_name, temperature=0)
        prompt = MODULE_SUMMARY_PROMPT.format(
            module_path=module_path,
            file_summaries=file_summaries,
        )
        result = await model.ainvoke(prompt)
        summary = result.content.strip()

        artefact = Artefact(
            key=f"module_summary:{module_path}",
            kind="module_summary",
            source_path=module_path,
            source_mtime=time.time(),
            generated_at=time.time(),
            model=model_name,
            content=summary,
        )
        write_artefact(artefact)
        logger.info("Generated module_summary for %s", module_path)
        return artefact
    except Exception as exc:
        logger.error("Failed module_summary for %s: %s", module_path, exc)
        return None


def discover_modules(repo_root: Path, min_files: int = 2) -> list[str]:
    """Find directories with enough Python files to warrant a module summary."""
    modules: list[str] = []
    for path in repo_root.rglob("__init__.py"):
        parent = path.parent
        # Skip venv, cache, dist dirs
        parts = parent.parts
        if any(p in parts for p in (".venv", "__pycache__", ".vaner", "dist", "build")):
            continue
        py_files = list(parent.glob("*.py"))
        if len(py_files) >= min_files:
            try:
                rel = str(parent.relative_to(repo_root))
                modules.append(rel)
            except ValueError:
                continue
    return modules
