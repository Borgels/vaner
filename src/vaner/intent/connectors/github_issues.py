# SPDX-License-Identifier: Apache-2.0
"""WS1 — GitHubIssuesAdapter (T3, opt-in).

Pulls open issues (and optionally milestones) from a configured
per-repo allowlist. Uses the ``gh`` CLI via subprocess so the adapter
inherits the user's existing ``gh auth`` session without Vaner having to
manage credentials. Users without ``gh`` installed see a one-time warning
and the adapter returns an empty candidate list — never fails the
daemon.

Per spec §12 this connector is **off** by default
(``config.sources.intent_artefacts.github_issues.enabled = false``) and
requires an explicit repo list. No wildcard access; no cloud traffic
unless the user opted in.

API surface kept narrow:

- ``gh issue list --repo OWNER/NAME --state open --json <fields>``
  for discovery.
- ``gh issue view NUM --repo OWNER/NAME --json <fields>`` for fetch.

Body JSON fields are mapped into ``RawArtefact.metadata`` in the shape
:mod:`vaner.intent.ingest.extract_github` expects:
``issue_number``, ``issue_state``, ``issue_labels`` (comma-joined),
``issue_assignees`` (comma-joined), ``repo``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from vaner.intent.adapter import ArtefactCandidate, RawArtefact
from vaner.intent.artefacts import SourceTier

_LOG = logging.getLogger(__name__)

_DISCOVERY_FIELDS: tuple[str, ...] = (
    "number",
    "title",
    "state",
    "updatedAt",
    "labels",
)

_VIEW_FIELDS: tuple[str, ...] = (
    "number",
    "title",
    "state",
    "body",
    "labels",
    "assignees",
    "updatedAt",
)


@dataclass(slots=True)
class GitHubIssuesAdapter:
    """T3 connector for GitHub issues. Opt-in, per-repo allowlisted."""

    repos: tuple[str, ...]
    include_closed: bool = False
    max_issues_per_repo: int = 200
    connector: str = "github_issues"
    tier: SourceTier = "T3"
    gh_binary: str = "gh"
    # Timeout for each ``gh`` invocation. Network hiccups shouldn't
    # block the daemon — the adapter returns what it got and logs the
    # rest.
    per_call_timeout_s: float = 30.0

    async def discover(self) -> Iterable[ArtefactCandidate]:
        if not self.repos:
            return []
        if shutil.which(self.gh_binary) is None:
            _LOG.warning(
                "github_issues: %r not on PATH; configure gh or disable config.sources.intent_artefacts.github_issues",
                self.gh_binary,
            )
            return []

        candidates: list[ArtefactCandidate] = []
        state_arg = "all" if self.include_closed else "open"
        for repo in self.repos:
            try:
                raw = await self._run_gh(
                    [
                        "issue",
                        "list",
                        "--repo",
                        repo,
                        "--state",
                        state_arg,
                        "--limit",
                        str(self.max_issues_per_repo),
                        "--json",
                        ",".join(_DISCOVERY_FIELDS),
                    ]
                )
            except _GhError as exc:
                _LOG.warning("github_issues: discovery failed for %s: %s", repo, exc)
                continue
            try:
                payload = json.loads(raw or "[]")
            except json.JSONDecodeError as exc:
                _LOG.warning("github_issues: non-json discovery output for %s: %s", repo, exc)
                continue
            if not isinstance(payload, list):
                continue
            for issue in payload:
                number = issue.get("number")
                title = issue.get("title") or ""
                state = issue.get("state") or "open"
                updated_at = _parse_timestamp(issue.get("updatedAt"))
                if number is None:
                    continue
                source_uri = f"github://{repo}/issues/{number}"
                candidates.append(
                    ArtefactCandidate(
                        source_uri=source_uri,
                        connector=self.connector,
                        tier=self.tier,
                        last_modified=updated_at,
                        title_hint=title,
                        metadata={
                            "repo": repo,
                            "issue_number": str(number),
                            "issue_state": str(state).lower(),
                        },
                    )
                )
        return candidates

    async def fetch(self, candidate: ArtefactCandidate) -> RawArtefact:
        repo = candidate.metadata.get("repo", "")
        number = candidate.metadata.get("issue_number", "")
        text = ""
        labels: list[str] = []
        assignees: list[str] = []
        state = candidate.metadata.get("issue_state", "open")
        if repo and number:
            try:
                raw = await self._run_gh(
                    [
                        "issue",
                        "view",
                        number,
                        "--repo",
                        repo,
                        "--json",
                        ",".join(_VIEW_FIELDS),
                    ]
                )
                payload = json.loads(raw or "{}")
            except (_GhError, json.JSONDecodeError) as exc:
                _LOG.warning("github_issues: view failed for %s#%s: %s", repo, number, exc)
                payload = {}
            if isinstance(payload, dict):
                text = str(payload.get("body") or "")
                state = str(payload.get("state") or state).lower()
                labels = [str(entry.get("name") or entry) for entry in (payload.get("labels") or []) if entry]
                assignees = [str(entry.get("login") or entry) for entry in (payload.get("assignees") or []) if entry]
        metadata = dict(candidate.metadata)
        metadata["issue_state"] = state
        if labels:
            metadata["issue_labels"] = ",".join(labels)
        if assignees:
            metadata["issue_assignees"] = ",".join(assignees)
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

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    async def _run_gh(self, args: list[str]) -> str:
        """Run ``gh`` with ``args`` and return stdout.

        Raises :class:`_GhError` on non-zero exit or timeout so the
        discover / fetch paths can log and continue instead of crashing
        the daemon.
        """

        proc = await asyncio.create_subprocess_exec(
            self.gh_binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.per_call_timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise _GhError("gh call timed out") from None
        if proc.returncode != 0:
            raise _GhError(f"gh exit {proc.returncode}: {stderr.decode('utf-8', errors='replace').strip()}")
        return stdout.decode("utf-8", errors="replace")


class _GhError(RuntimeError):
    """Internal marker for gh CLI failures — logged and absorbed, never
    surfaced to the daemon."""


def _parse_timestamp(value: object) -> float:
    """Best-effort ISO-8601 → epoch-seconds conversion. Returns 0.0 on
    failure so downstream sorting degrades gracefully."""

    if not isinstance(value, str):
        return 0.0
    try:
        # GitHub returns ``2026-04-25T15:22:30Z``.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0
