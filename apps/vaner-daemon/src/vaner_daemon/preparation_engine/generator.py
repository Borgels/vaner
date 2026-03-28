from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from langchain_ollama import ChatOllama
from vaner_tools.artefact_store import Artefact, write_artefact

logger = logging.getLogger("vaner.generator")

FILE_SUMMARY_PROMPT = """Summarize this source file for a developer context system.
Be terse and keyword-rich. Explicitly name: classes, functions, key constants, important imports.
Focus on what this file DOES and what it EXPORTS.
Do not include file path or language in summary.

File: {path}
---
{content}
---
Summary:"""

DIFF_SUMMARY_PROMPT = """Summarize the following git diff for a developer context system.
State clearly: what changed, which modules/functions were affected, what the likely intent was.
Be concise.

{diff}
---
Summary:"""


async def generate_file_summary(
    source_path: Path,
    repo_root: Path,
    model_name: str = "qwen2.5-coder:32b",
) -> Artefact | None:
    try:
        content = source_path.read_text(errors="replace")[:8000]
        rel_path = str(source_path.relative_to(repo_root))
        model = ChatOllama(model=model_name, temperature=0)
        prompt = FILE_SUMMARY_PROMPT.format(path=rel_path, content=content)
        result = await model.ainvoke(prompt)
        summary = result.content.strip()
        artefact = write_artefact(
            cache_root=repo_root / ".vaner" / "cache",
            source_path=source_path,
            repo_root=repo_root,
            kind="file_summary",
            content=summary,
        )
        logger.info("Generated file_summary for %s", rel_path)
        return artefact
    except Exception as exc:
        logger.error("Failed file_summary for %s: %s", source_path, exc)
        return None


async def generate_diff_summary(
    repo_root: Path,
    model_name: str = "qwen2.5-coder:32b",
) -> Artefact | None:
    try:
        stat = subprocess.check_output(
            ["git", "-C", str(repo_root), "diff", "HEAD", "--stat"],
            text=True,
            timeout=10,
        )
        diff = subprocess.check_output(
            ["git", "-C", str(repo_root), "diff", "HEAD"],
            text=True,
            timeout=10,
        )[:3000]
        combined = f"Stat:\n{stat}\n\nDiff:\n{diff}"
        model = ChatOllama(model=model_name, temperature=0)
        prompt = DIFF_SUMMARY_PROMPT.format(diff=combined)
        result = await model.ainvoke(prompt)
        summary = result.content.strip()
        # Use a sentinel path for the repo-level diff artefact
        sentinel = repo_root / "diff_summary_sentinel"
        sentinel.touch()
        artefact = write_artefact(
            cache_root=repo_root / ".vaner" / "cache",
            source_path=sentinel,
            repo_root=repo_root,
            kind="diff_summary",
            content=summary,
        )
        sentinel.unlink(missing_ok=True)
        logger.info("Generated diff_summary for %s", repo_root)
        return artefact
    except Exception as exc:
        logger.error("Failed diff_summary: %s", exc)
        return None
