# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

from vaner.broker.assembler import assemble_context_package
from vaner.broker.selector import select_artefacts
from vaner.cli.commands.config import load_config
from vaner.daemon.runner import VanerDaemon
from vaner.daemon.signals.git_reader import read_git_state
from vaner.models.config import VanerConfig
from vaner.models.context import ContextPackage
from vaner.store.artefacts import ArtefactStore


def _resolve_repo_root(repo: Path | str | None) -> Path:
    if isinstance(repo, Path):
        return repo.resolve()
    if isinstance(repo, str):
        return Path(repo).resolve()
    return Path.cwd()


def _resolve_config(repo_root: Path, config: VanerConfig | None) -> VanerConfig:
    return config if config is not None else load_config(repo_root)


def _write_last_context(repo_root: Path, prompt: str, package: ContextPackage) -> None:
    inspect_path = repo_root / ".vaner" / "runtime" / "last_context.md"
    inspect_path.parent.mkdir(parents=True, exist_ok=True)
    inspect_lines = [
        f"prompt: {prompt}",
        f"token_used: {package.token_used}/{package.token_budget}",
        "",
    ]
    for item in package.selections:
        inspect_lines.append(
            f"- {item.artefact_key} score={item.score:.2f} stale={item.stale} tokens={item.token_count} rationale={item.rationale}"
        )
    inspect_path.write_text("\n".join(inspect_lines), encoding="utf-8")


async def aprepare(repo: Path | str | None = None, config: VanerConfig | None = None) -> int:
    repo_root = _resolve_repo_root(repo)
    resolved = _resolve_config(repo_root, config)
    daemon = VanerDaemon(resolved)
    return await daemon.run_once()


async def aquery(
    prompt: str,
    repo: Path | str | None = None,
    *,
    config: VanerConfig | None = None,
    max_tokens: int | None = None,
    top_n: int = 8,
) -> ContextPackage:
    repo_root = _resolve_repo_root(repo)
    resolved = _resolve_config(repo_root, config)
    store = ArtefactStore(resolved.store_path)
    await store.initialize()

    artefacts = await store.list(limit=50)
    if not artefacts:
        await aprepare(repo_root, resolved)
        artefacts = await store.list(limit=50)

    git_state = read_git_state(repo_root)
    preferred_paths = {
        line.strip()
        for line in (git_state.get("recent_diff", "") + "\n" + git_state.get("staged", "")).splitlines()
        if line.strip()
    }
    working_set = await store.get_latest_working_set()
    preferred_keys = set(working_set.artefact_keys) if working_set is not None else set()

    selected = select_artefacts(
        prompt,
        artefacts,
        top_n=top_n,
        preferred_paths=preferred_paths,
        preferred_keys=preferred_keys,
    )
    package = assemble_context_package(
        prompt,
        selected,
        max_tokens if max_tokens is not None else resolved.max_context_tokens,
        repo_root=resolved.repo_root,
        max_age_seconds=resolved.max_age_seconds,
    )
    for artefact in selected:
        await store.mark_accessed(artefact.key)
    _write_last_context(repo_root, prompt, package)
    return package


async def ainspect(repo: Path | str | None = None, config: VanerConfig | None = None) -> str:
    repo_root = _resolve_repo_root(repo)
    resolved = _resolve_config(repo_root, config)
    store = ArtefactStore(resolved.store_path)
    await store.initialize()
    artefacts = await store.list(limit=50)
    if not artefacts:
        return "No artefacts cached."
    return "\n".join(f"{artefact.key} kind={artefact.kind.value} generated_at={artefact.generated_at:.0f}" for artefact in artefacts)


def prepare(repo: Path | str | None = None, config: VanerConfig | None = None) -> int:
    return asyncio.run(aprepare(repo, config))


def query(
    prompt: str,
    repo: Path | str | None = None,
    *,
    config: VanerConfig | None = None,
    max_tokens: int | None = None,
    top_n: int = 8,
) -> ContextPackage:
    return asyncio.run(aquery(prompt, repo, config=config, max_tokens=max_tokens, top_n=top_n))


def inspect(repo: Path | str | None = None, config: VanerConfig | None = None) -> str:
    return asyncio.run(ainspect(repo, config))


def inspect_last(repo: Path | str | None = None) -> str:
    repo_root = _resolve_repo_root(repo)
    path = repo_root / ".vaner" / "runtime" / "last_context.md"
    if not path.exists():
        return "No context decisions recorded yet."
    return path.read_text(encoding="utf-8")


def forget(repo: Path | str | None = None) -> int:
    repo_root = _resolve_repo_root(repo)
    removed = 0
    for filename in ["store.db", "telemetry.db"]:
        path = repo_root / ".vaner" / filename
        if path.exists():
            path.unlink()
            removed += 1
    runtime = repo_root / ".vaner" / "runtime"
    if runtime.exists():
        for item in runtime.iterdir():
            if item.is_file():
                item.unlink()
                removed += 1
    return removed
