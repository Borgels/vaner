from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from vaner_runtime.job_queue import Priority
from vaner_tools.artefact_store import Artefact, is_stale

from .triggers import PrepTrigger

WATCHED_EXTENSIONS = {".py", ".ts", ".js", ".tsx", ".md", ".toml", ".yaml", ".yml"}


@dataclass
class ArtifactJob:
    job_id: str
    artifact_kind: Literal["file_summary", "diff_summary"]
    source_path: str
    context_key: str
    priority: Priority


class PreparationPlanner:
    def __init__(self, repo_root: Path, max_jobs: int = 10):
        self._repo_root = repo_root
        self._max_jobs = max_jobs

    def plan(self, trigger: PrepTrigger, existing_artifacts: list[Artefact]) -> list[ArtifactJob]:
        jobs: list[ArtifactJob] = []
        existing_by_path = {
            a.source_path: a
            for a in existing_artifacts
            if a.kind == "file_summary"
        }

        # diff_summary at CRITICAL priority for commit/branch switch
        if trigger.reason in ("git_commit", "branch_switch"):
            jobs.append(ArtifactJob(
                job_id=str(uuid.uuid4()),
                artifact_kind="diff_summary",
                source_path=str(self._repo_root),
                context_key=trigger.context_key,
                priority=Priority.CRITICAL,
            ))

        # file_summary for stale/missing active files
        for f in trigger.active_files:
            if len(jobs) >= self._max_jobs:
                break
            if Path(f).suffix not in WATCHED_EXTENSIONS:
                continue
            existing = existing_by_path.get(f)
            if existing is None or is_stale(existing):
                jobs.append(ArtifactJob(
                    job_id=str(uuid.uuid4()),
                    artifact_kind="file_summary",
                    source_path=f,
                    context_key=trigger.context_key,
                    priority=Priority.HIGH,
                ))

        return jobs[: self._max_jobs]
