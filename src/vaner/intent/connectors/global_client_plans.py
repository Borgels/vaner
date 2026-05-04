# SPDX-License-Identifier: Apache-2.0
"""Workspace-matched global AI-client plan connector."""

from __future__ import annotations

import fnmatch
import hashlib
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from vaner.intent.adapter import ArtefactCandidate, RawArtefact
from vaner.intent.artefacts import SourceTier

_PLAN_EXTS = frozenset({".md", ".markdown", ".mdx", ".txt", ".rst"})
_DEFAULT_EXCLUDELIST = ("*.env", "**/credentials*", "**/.secrets*", "**/*.secret")


@dataclass(slots=True)
class CandidateDecision:
    path: str
    client: str
    status: str
    reason: str
    matched_by: str | None = None


@dataclass(slots=True)
class GlobalClientPlansAdapter:
    workspace_root: Path
    selected_clients: tuple[str, ...] = ("claude-code", "codex-cli")
    allowed_roots: tuple[str, ...] = ()
    include_rollout_summaries: bool = False
    max_file_bytes: int = 2 * 1024 * 1024
    max_files: int = 500
    excludelist: tuple[str, ...] = field(default=_DEFAULT_EXCLUDELIST)
    connector: str = "global_client_plans"
    tier: SourceTier = "T2"

    async def discover(self) -> Iterable[ArtefactCandidate]:
        decisions = self.inspect_candidates()
        accepted = [item for item in decisions if item.status == "accepted"][: max(1, self.max_files)]
        candidates: list[ArtefactCandidate] = []
        for item in accepted:
            path = Path(item.path)
            try:
                stat = path.stat()
            except OSError:
                continue
            candidates.append(
                ArtefactCandidate(
                    source_uri=f"file://{path}",
                    connector=self.connector,
                    tier=self.tier,
                    last_modified=stat.st_mtime,
                    title_hint=path.name,
                    metadata={
                        "client": item.client,
                        "match_reason": item.reason,
                        "matched_by": item.matched_by or "",
                        "privacy_zone": "global_client_plan_workspace_matched",
                    },
                )
            )
        return candidates

    async def fetch(self, candidate: ArtefactCandidate) -> RawArtefact:
        path = _path_from_uri(candidate.source_uri)
        text = path.read_text(encoding="utf-8", errors="replace")
        metadata = dict(candidate.metadata)
        metadata["content_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return RawArtefact(
            source_uri=candidate.source_uri,
            connector=self.connector,
            tier=self.tier,
            text=text,
            last_modified=candidate.last_modified,
            title_hint=candidate.title_hint,
            metadata=metadata,
        )

    def identify(self, raw: RawArtefact) -> str:
        return raw.source_uri

    def inspect_candidates(self) -> list[CandidateDecision]:
        roots = self._candidate_roots()
        decisions: list[CandidateDecision] = []
        seen: set[Path] = set()
        for client, root in roots:
            if not root.exists():
                continue
            paths = root.rglob("*") if root.is_dir() else iter((root,))
            for path in paths:
                if len(decisions) >= max(1, self.max_files) * 4:
                    break
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved in seen or not resolved.is_file():
                    continue
                seen.add(resolved)
                decision = self._inspect_one(client, resolved)
                if decision is not None:
                    decisions.append(decision)
        return decisions

    def _candidate_roots(self) -> list[tuple[str, Path]]:
        home = Path.home()
        roots: list[tuple[str, Path]] = []
        selected = set(self.selected_clients)
        if "claude-code" in selected or "claude" in selected:
            roots.append(("claude-code", home / ".claude" / "plans"))
            roots.append(("claude-code", home / ".claude" / "projects"))
        if "codex-cli" in selected or "codex" in selected:
            roots.append(("codex-cli", home / ".codex" / "plans"))
            if self.include_rollout_summaries:
                roots.append(("codex-cli", home / ".codex" / "memories" / "rollout_summaries"))
        for raw in self.allowed_roots:
            if raw.strip():
                roots.append(("custom", Path(raw).expanduser()))
        return roots

    def _inspect_one(self, client: str, path: Path) -> CandidateDecision | None:
        if path.suffix.lower() not in _PLAN_EXTS:
            return None
        path_text = str(path)
        if "/.claude/projects/" in path_text and "/memory/" not in path_text and "/plans/" not in path_text:
            return None
        if self._is_excluded(path):
            return CandidateDecision(str(path), client, "skipped", "excluded")
        try:
            stat = path.stat()
        except OSError:
            return None
        if stat.st_size <= 0 or stat.st_size > max(1024, self.max_file_bytes):
            return CandidateDecision(str(path), client, "skipped", "size_out_of_range")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return CandidateDecision(str(path), client, "skipped", "unreadable")
        matched_by = self._match_workspace(path, text)
        if matched_by:
            return CandidateDecision(str(path), client, "accepted", "workspace_match", matched_by)
        return CandidateDecision(str(path), client, "skipped", "no_workspace_match")

    def _match_workspace(self, path: Path, text: str) -> str | None:
        root = self.workspace_root.resolve()
        root_text = str(root)
        name = root.name
        haystack = f"{path}\n{text[:200_000]}"
        if root_text and root_text in haystack:
            return "workspace_path"
        if name and name in haystack:
            return "workspace_name"
        remote_slug = _git_remote_slug(root)
        if remote_slug and remote_slug in haystack:
            return "git_remote_slug"
        return None

    def _is_excluded(self, path: Path) -> bool:
        path_text = str(path)
        for pattern in self.excludelist or _DEFAULT_EXCLUDELIST:
            if fnmatch.fnmatch(path_text, pattern) or fnmatch.fnmatch(path.name, pattern):
                return True
        return False


def _git_remote_slug(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    url = result.stdout.strip()
    if not url:
        return None
    slug = url.rstrip("/").removesuffix(".git")
    if ":" in slug and "/" in slug.rsplit(":", 1)[-1]:
        slug = slug.rsplit(":", 1)[-1]
    else:
        parts = slug.split("/")
        slug = "/".join(parts[-2:]) if len(parts) >= 2 else slug
    return slug or None


def _path_from_uri(source_uri: str) -> Path:
    if source_uri.startswith("file://"):
        return Path(source_uri[len("file://") :])
    return Path(source_uri)
