# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

from vaner.daemon.engine.generator import (
    agenerate_diff_summary,
    agenerate_file_summary,
)
from vaner.daemon.engine.planner import plan_targets
from vaner.daemon.engine.scorer import score_paths
from vaner.daemon.signals.fs_watcher import RepoChangeWatcher, scan_repo_files
from vaner.daemon.signals.git_reader import read_git_diff, read_git_state
from vaner.models.config import VanerConfig
from vaner.models.session import WorkingSet
from vaner.models.signal import SignalEvent
from vaner.store.artefacts import ArtefactStore
from vaner.store.telemetry import TelemetryStore

logger = logging.getLogger(__name__)


class VanerDaemon:
    def __init__(self, config: VanerConfig) -> None:
        self.config = config
        self.store = ArtefactStore(config.store_path)
        self.telemetry = TelemetryStore(config.telemetry_path)
        self._running = False

    async def initialize(self) -> None:
        await self.store.initialize()
        await self.telemetry.initialize()

    async def run_once(self, changed_files: list[Path] | None = None) -> int:
        await self.initialize()
        repo_root = self.config.repo_root
        include = self.config.privacy.allowed_paths or None
        files = changed_files if changed_files is not None else scan_repo_files(repo_root, include_paths=include)
        git_state = read_git_state(repo_root)
        recent_paths = {line.strip() for line in git_state.get("recent_diff", "").splitlines() if line.strip()}
        staged_paths = {line.strip() for line in git_state.get("staged", "").splitlines() if line.strip()}
        signals = [
            SignalEvent(
                id=str(uuid.uuid4()),
                source="fs_scan",
                kind="file_seen",
                timestamp=time.time(),
                payload={
                    "path": str(path.relative_to(repo_root)),
                    "corpus_id": "repo",
                    "privacy_zone": "project_local",
                },
            )
            for path in files
        ]
        git_paths = sorted(recent_paths | staged_paths)
        signals.extend(
            SignalEvent(
                id=str(uuid.uuid4()),
                source="git",
                kind="git_changed",
                timestamp=time.time(),
                payload={
                    "path": rel_path,
                    "corpus_id": "repo",
                    "privacy_zone": "project_local",
                },
            )
            for rel_path in git_paths
        )
        for signal in signals:
            await self.store.insert_signal_event(signal)
        targets = plan_targets(
            repo_root,
            signals,
            self.config.privacy.allowed_paths,
            self.config.privacy.excluded_patterns,
        )
        prioritized_abs = {str((repo_root / rel_path).resolve()) for rel_path in git_paths}
        ranked_targets = [path for path, _ in score_paths(targets, prioritized_paths=prioritized_abs)]
        max_per_cycle = max(1, self.config.generation.max_generations_per_cycle)
        concurrent_limit = max(1, self.config.generation.max_concurrent_generations)
        generation_semaphore = asyncio.Semaphore(concurrent_limit)
        written = 0
        generated_files = []

        async def _generate_with_limit(target: Path):
            async with generation_semaphore:
                return await agenerate_file_summary(
                    target,
                    repo_root,
                    model_name=self.config.backend.model,
                    redact_patterns=self.config.privacy.redact_patterns,
                    config=self.config,
                )

        generation_tasks = [_generate_with_limit(target) for target in ranked_targets[:max_per_cycle]]
        generated_results = await asyncio.gather(*generation_tasks, return_exceptions=True)
        for result in generated_results:
            if isinstance(result, Exception):
                logger.warning("Skipping failed file summary generation", exc_info=result)
                continue
            result.metadata.setdefault("corpus_id", "repo")
            result.metadata.setdefault("privacy_zone", "project_local")
            await self.store.upsert(result)
            generated_files.append(result)
            written += 1

        for rel_path in sorted(recent_paths):
            diff_text = read_git_diff(repo_root, rel_path)
            if not diff_text:
                continue
            diff_artefact = await agenerate_diff_summary(
                repo_root,
                rel_path,
                diff_text,
                model_name=self.config.backend.model,
                redact_patterns=self.config.privacy.redact_patterns,
                config=self.config,
            )
            diff_artefact.metadata.setdefault("corpus_id", "repo")
            diff_artefact.metadata.setdefault("privacy_zone", "project_local")
            await self.store.upsert(diff_artefact)
            written += 1

        working_keys = [f"file_summary:{path}" for path in sorted(recent_paths | staged_paths)]
        working_keys.extend(artefact.key for artefact in generated_files[:8])
        working_set = WorkingSet(
            session_id=f"{repo_root}:{git_state.get('branch') or 'default'}",
            artefact_keys=sorted(set(working_keys)),
            updated_at=time.time(),
            reason="git_and_recency",
        )
        await self.store.upsert_working_set(working_set)
        await self.telemetry.record("artefacts_written", float(written))
        return written

    async def run_forever(self, interval_seconds: int = 15) -> None:
        self._running = True
        await self.initialize()
        repo_root = self.config.repo_root
        watcher = RepoChangeWatcher(repo_root)
        watcher.start()
        try:
            while self._running:
                changed_files = watcher.drain_changes()
                if changed_files:
                    await self.run_once(changed_files=changed_files)
                else:
                    await self.run_once()
                await asyncio.sleep(interval_seconds)
        finally:
            watcher.stop()

    def stop(self) -> None:
        self._running = False
