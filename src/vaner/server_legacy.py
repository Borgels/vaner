# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

from vaner.cli.commands.config import load_config
from vaner.engine import build_default_engine
from vaner.models.config import VanerConfig
from vaner.models.context import ContextPackage
from vaner.models.decision import DecisionRecord
from vaner.telemetry.metrics import MetricsStore


def _resolve_repo_root(repo: Path | str | None) -> Path:
    if isinstance(repo, Path):
        return repo.resolve()
    if isinstance(repo, str):
        return Path(repo).resolve()
    return Path.cwd()


def _resolve_config(repo_root: Path, config: VanerConfig | None) -> VanerConfig:
    return config if config is not None else load_config(repo_root)


def _write_last_context(repo_root: Path, record: DecisionRecord) -> None:
    record.write(repo_root)
    inspect_path = repo_root / ".vaner" / "runtime" / "last_context.md"
    inspect_path.write_text(record.to_legacy_markdown(), encoding="utf-8")


async def aprepare(repo: Path | str | None = None, config: VanerConfig | None = None) -> int:
    repo_root = _resolve_repo_root(repo)
    engine = build_default_engine(repo_root, _resolve_config(repo_root, config))
    return await engine.prepare()


async def aquery(
    prompt: str,
    repo: Path | str | None = None,
    *,
    config: VanerConfig | None = None,
    max_tokens: int | None = None,
    top_n: int = 8,
) -> ContextPackage:
    repo_root = _resolve_repo_root(repo)
    try:
        store = MetricsStore(repo_root / ".vaner" / "metrics.db")
        await store.initialize()
        await store.increment_mode_usage("sdk")
    except Exception:
        pass
    engine = build_default_engine(repo_root, _resolve_config(repo_root, config))
    package = await engine.query(prompt, max_tokens=max_tokens, top_n=top_n)
    decision_record = engine.get_last_decision_record()
    if decision_record is not None:
        _write_last_context(repo_root, decision_record)
    return package


async def apredict(
    repo: Path | str | None = None,
    *,
    config: VanerConfig | None = None,
    top_k: int = 5,
) -> list[dict[str, object]]:
    repo_root = _resolve_repo_root(repo)
    engine = build_default_engine(repo_root, _resolve_config(repo_root, config))
    predictions = await engine.predict(top_k=top_k)
    return [{"key": item.key, "score": item.score, "reason": item.reason} for item in predictions]


async def aprecompute(
    repo: Path | str | None = None,
    *,
    config: VanerConfig | None = None,
) -> int:
    repo_root = _resolve_repo_root(repo)
    try:
        store = MetricsStore(repo_root / ".vaner" / "metrics.db")
        await store.initialize()
        await store.increment_mode_usage("precompute")
    except Exception:
        pass
    engine = build_default_engine(repo_root, _resolve_config(repo_root, config))
    return await engine.precompute_cycle()


async def ainspect(repo: Path | str | None = None, config: VanerConfig | None = None) -> str:
    repo_root = _resolve_repo_root(repo)
    resolved = _resolve_config(repo_root, config)
    engine = build_default_engine(repo_root, resolved)
    store = engine.store
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


def predict(
    repo: Path | str | None = None,
    *,
    config: VanerConfig | None = None,
    top_k: int = 5,
) -> list[dict[str, object]]:
    return asyncio.run(apredict(repo, config=config, top_k=top_k))


def precompute(
    repo: Path | str | None = None,
    *,
    config: VanerConfig | None = None,
) -> int:
    return asyncio.run(aprecompute(repo, config=config))


def inspect(repo: Path | str | None = None, config: VanerConfig | None = None) -> str:
    return asyncio.run(ainspect(repo, config))


def inspect_last(repo: Path | str | None = None) -> str:
    repo_root = _resolve_repo_root(repo)
    path = repo_root / ".vaner" / "runtime" / "last_context.md"
    if not path.exists():
        return "No context decisions recorded yet."
    return path.read_text(encoding="utf-8")


def inspect_last_decision(repo: Path | str | None = None) -> DecisionRecord | None:
    repo_root = _resolve_repo_root(repo)
    return DecisionRecord.read_latest(repo_root)


def inspect_decision(repo: Path | str | None = None, decision_id: str | None = None) -> DecisionRecord | None:
    repo_root = _resolve_repo_root(repo)
    if decision_id is None:
        return DecisionRecord.read_latest(repo_root)
    return DecisionRecord.read_by_id(repo_root, decision_id)


def list_decisions(repo: Path | str | None = None, limit: int = 20) -> list[str]:
    repo_root = _resolve_repo_root(repo)
    return DecisionRecord.list_recent_ids(repo_root, limit=limit)


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
