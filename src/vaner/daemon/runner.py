# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from pathlib import Path

from vaner.daemon.engine.generator import (
    agenerate_diff_summary,
    agenerate_file_summary,
    generate_dir_summary,
    generate_repo_index,
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
        files = changed_files if changed_files is not None else scan_repo_files(repo_root)
        git_state = read_git_state(repo_root)
        recent_paths = {line.strip() for line in git_state.get("recent_diff", "").splitlines() if line.strip()}
        staged_paths = {line.strip() for line in git_state.get("staged", "").splitlines() if line.strip()}
        signals = [
            SignalEvent(
                id=str(uuid.uuid4()),
                source="fs_scan",
                kind="file_seen",
                timestamp=time.time(),
                payload={"path": str(path.relative_to(repo_root))},
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
                payload={"path": rel_path},
            )
            for rel_path in git_paths
        )
        targets = plan_targets(
            repo_root,
            signals,
            self.config.privacy.allowed_paths,
            self.config.privacy.excluded_patterns,
        )
        prioritized_abs = {str((repo_root / rel_path).resolve()) for rel_path in git_paths}
        ranked_targets = [path for path, _ in score_paths(targets, prioritized_paths=prioritized_abs)]
        written = 0
        generated_files = []
        for target in ranked_targets:
            artefact = await agenerate_file_summary(
                target,
                repo_root,
                model_name=self.config.backend.model,
                redact_patterns=self.config.privacy.redact_patterns,
                config=self.config,
            )
            await self.store.upsert(artefact)
            generated_files.append(artefact)
            written += 1

        by_parent: dict[Path, list] = defaultdict(list)
        for file_summary in generated_files:
            by_parent[(repo_root / file_summary.source_path).parent].append(file_summary)
        for directory, child_summaries in by_parent.items():
            if len(child_summaries) < 3:
                continue
            dir_artefact = generate_dir_summary(
                directory,
                repo_root,
                child_summaries,
                model_name=self.config.backend.model,
            )
            await self.store.upsert(dir_artefact)
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
            await self.store.upsert(diff_artefact)
            written += 1

        repo_index = generate_repo_index(repo_root, files, model_name=self.config.backend.model)
        await self.store.upsert(repo_index)
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
