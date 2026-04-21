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
from vaner.daemon.engine.scenario_builder import build_scenarios
from vaner.daemon.engine.scorer import score_paths
from vaner.daemon.signals.fs_watcher import RepoChangeWatcher, scan_repo_files
from vaner.daemon.signals.git_reader import read_git_diff, read_git_state
from vaner.events import cycle_scope
from vaner.events import publish as publish_event
from vaner.models.artefact import Artefact
from vaner.models.config import VanerConfig
from vaner.models.session import WorkingSet
from vaner.models.signal import SignalEvent
from vaner.store.artefacts import ArtefactStore
from vaner.store.scenarios import ScenarioStore
from vaner.store.telemetry import TelemetryStore

logger = logging.getLogger(__name__)


class VanerDaemon:
    def __init__(self, config: VanerConfig) -> None:
        self.config = config
        self.store = ArtefactStore(config.store_path)
        self.scenarios = ScenarioStore(config.repo_root / ".vaner" / "scenarios.db")
        self.telemetry = TelemetryStore(config.telemetry_path)
        self._running = False

    async def initialize(self) -> None:
        await self.store.initialize()
        await self.scenarios.initialize()
        await self.telemetry.initialize()

    async def run_once(self, changed_files: list[Path] | None = None) -> int:
        await self.initialize()
        cycle_id = f"cyc_{uuid.uuid4().hex[:8]}"
        cycle_started_at = time.monotonic()
        with cycle_scope(cycle_id):
            return await self._run_once_impl(cycle_id, cycle_started_at, changed_files)

    async def _run_once_impl(
        self,
        cycle_id: str,
        cycle_started_at: float,
        changed_files: list[Path] | None,
    ) -> int:
        publish_event("system", "cycle.start", {"msg": f"cycle {cycle_id} started"}, cycle_id=cycle_id)
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
        fs_scan_count = sum(1 for signal in signals if signal.source == "fs_scan")
        git_count = len(git_paths)
        publish_event(
            "signals",
            "signal.ingest",
            {
                "msg": f"{fs_scan_count} file_seen, {git_count} git_changed",
                "fs_scan": fs_scan_count,
                "git_changed": git_count,
            },
            cycle_id=cycle_id,
        )
        targets = plan_targets(
            repo_root,
            signals,
            self.config.privacy.allowed_paths,
            self.config.privacy.excluded_patterns,
        )
        prioritized_abs = {str((repo_root / rel_path).resolve()) for rel_path in git_paths}
        ranked_targets = [path for path, _ in score_paths(targets, prioritized_paths=prioritized_abs)]
        max_per_cycle = max(1, self.config.generation.max_generations_per_cycle)
        planned_paths = [
            str(target.relative_to(repo_root)) if target.is_relative_to(repo_root) else str(target)
            for target in ranked_targets[:max_per_cycle]
        ]
        publish_event(
            "targets",
            "target.planned",
            {
                "msg": f"{len(planned_paths)} targets planned",
                "count": len(planned_paths),
                "paths": planned_paths,
            },
            cycle_id=cycle_id,
        )
        concurrent_limit = max(1, self.config.generation.max_concurrent_generations)
        generation_semaphore = asyncio.Semaphore(concurrent_limit)
        written = 0
        generated_files = []

        async def _generate_with_limit(target: Path) -> Artefact:
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
            if isinstance(result, BaseException):
                logger.warning("Skipping failed file summary generation", exc_info=result)
                continue
            artefact = result
            artefact.metadata.setdefault("corpus_id", "repo")
            artefact.metadata.setdefault("privacy_zone", "project_local")
            await self.store.upsert(artefact)
            generated_files.append(artefact)
            written += 1
            publish_event(
                "artefacts",
                "artefact.upsert",
                {
                    "msg": f"{artefact.kind.value}: {artefact.source_path}",
                    "key": artefact.key,
                    "kind": artefact.kind.value,
                },
                path=artefact.source_path,
                cycle_id=cycle_id,
            )

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
            publish_event(
                "artefacts",
                "artefact.upsert",
                {
                    "msg": f"{diff_artefact.kind.value}: {diff_artefact.source_path}",
                    "key": diff_artefact.key,
                    "kind": diff_artefact.kind.value,
                },
                path=diff_artefact.source_path,
                cycle_id=cycle_id,
            )

        working_keys = [f"file_summary:{path}" for path in sorted(recent_paths | staged_paths)]
        working_keys.extend(artefact.key for artefact in generated_files[:8])
        working_set = WorkingSet(
            session_id=f"{repo_root}:{git_state.get('branch') or 'default'}",
            artefact_keys=sorted(set(working_keys)),
            updated_at=time.time(),
            reason="git_and_recency",
        )
        await self.store.upsert_working_set(working_set)
        latest_artefacts = await self.store.list(limit=max(50, written * 2))
        scenarios = build_scenarios(self.config.repo_root, latest_artefacts, sorted(recent_paths | staged_paths))
        for scenario in scenarios:
            await self.scenarios.upsert(scenario)
        await self.scenarios.mark_stale()
        await self.telemetry.record("artefacts_written", float(written))
        duration_ms = (time.monotonic() - cycle_started_at) * 1000.0
        publish_event(
            "system",
            "cycle.end",
            {
                "msg": f"cycle {cycle_id} wrote {written} artefacts in {duration_ms:.0f}ms",
                "written": written,
                "duration_ms": round(duration_ms, 2),
            },
            cycle_id=cycle_id,
        )
        return written

    async def run_forever(self, interval_seconds: int = 15) -> None:
        self._running = True
        await self.initialize()
        repo_root = self.config.repo_root
        watcher = RepoChangeWatcher(repo_root)
        watcher.start()
        session_started = time.monotonic()
        session_budget_minutes = self.config.compute.max_session_minutes
        session_deadline: float | None = None
        if session_budget_minutes is not None and session_budget_minutes > 0:
            session_deadline = session_started + float(session_budget_minutes) * 60.0
            logger.info("Daemon session budget: %s minute(s)", session_budget_minutes)
        try:
            while self._running:
                if session_deadline is not None and time.monotonic() >= session_deadline:
                    logger.info("Daemon session budget exhausted after %s minute(s); exiting cleanly.", session_budget_minutes)
                    break
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
