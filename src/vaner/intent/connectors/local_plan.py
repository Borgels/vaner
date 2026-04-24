# SPDX-License-Identifier: Apache-2.0
"""WS1 — LocalPlanAdapter (T1, auto-enabled).

Walks a configured allowlist of plan folders inside a workspace and
yields :class:`ArtefactCandidate` records for every file that looks
plausibly plan-shaped. Classification is the pipeline's job, not the
connector's — the adapter's filter here is coarse (extension + modest
size band + excludelist) and the classifier makes the accept/reject
call.

Default allowlist — the spec §12 ``local_plan`` defaults:

- ``.claude/plans/``
- ``.cursor/plans/``
- ``docs/plans/``
- ``docs/roadmap*`` (prefix glob)
- ``AGENTS.md`` (root)
- ``ARCHITECTURE.md`` (root)

Default excludelist (spec §12): ``.env``, ``**/credentials*``,
``**/.secrets*``. Users can extend both via
``config.sources.intent_artefacts.local_plan``.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from vaner.intent.adapter import ArtefactCandidate, RawArtefact
from vaner.intent.artefacts import SourceTier

_LOG = logging.getLogger(__name__)

# File extensions we even *consider* for intent-bearing artefacts. Binary
# and code-only extensions are skipped outright.
_PLAN_EXTS: frozenset[str] = frozenset(
    {
        ".md",
        ".markdown",
        ".mdx",
        ".txt",
        ".rst",
    }
)

# Per-file size bounds. Tiny files rarely carry meaningful plan structure;
# huge files are almost never plans. Consistent with the classifier's
# size-band tuning (see ``classifier.PLAN_WORD_CEILING``).
_MIN_FILE_BYTES = 16
_MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB


DEFAULT_ALLOWLIST: tuple[str, ...] = (
    ".claude/plans",
    ".cursor/plans",
    "docs/plans",
    "docs/roadmap*",
    "AGENTS.md",
    "ARCHITECTURE.md",
)

DEFAULT_EXCLUDELIST: tuple[str, ...] = (
    ".env",
    "**/credentials*",
    "**/.secrets*",
    "**/*.secret",
)


@dataclass(slots=True)
class LocalPlanAdapter:
    """T1 (auto-enabled) connector for local plan folders.

    Implements :class:`IntentArtefactSource`. The adapter is stateless
    aside from the workspace root + configured allow/exclude lists; each
    :meth:`discover` call re-scans the filesystem.
    """

    workspace_root: Path
    allowlist: tuple[str, ...] = field(default=DEFAULT_ALLOWLIST)
    excludelist: tuple[str, ...] = field(default=DEFAULT_EXCLUDELIST)
    connector: str = "local_plan"
    tier: SourceTier = "T1"

    async def discover(self) -> Iterable[ArtefactCandidate]:
        """Enumerate candidate plan files under the workspace.

        Cheap: walks the allowlisted subtrees only, filters by extension
        and size, skips anything matching the excludelist. No content
        read.
        """

        root = self.workspace_root.resolve()
        seen: set[Path] = set()
        candidates: list[ArtefactCandidate] = []
        for entry in self.allowlist:
            for path in self._expand_entry(root, entry):
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                # Reject escape attempts.
                try:
                    resolved.relative_to(root)
                except ValueError:
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                if not resolved.is_file():
                    continue
                if resolved.suffix.lower() not in _PLAN_EXTS:
                    continue
                if self._is_excluded(root, resolved):
                    continue
                try:
                    stat = resolved.stat()
                except OSError:
                    continue
                if stat.st_size < _MIN_FILE_BYTES or stat.st_size > _MAX_FILE_BYTES:
                    continue
                rel = str(resolved.relative_to(root))
                candidates.append(
                    ArtefactCandidate(
                        source_uri=f"file://{resolved}",
                        connector=self.connector,
                        tier=self.tier,
                        last_modified=stat.st_mtime,
                        title_hint=resolved.name,
                        metadata={"workspace_rel_path": rel},
                    )
                )
        return candidates

    async def fetch(self, candidate: ArtefactCandidate) -> RawArtefact:
        """Read the candidate file's text body."""

        path = _path_from_uri(candidate.source_uri)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            _LOG.warning("local_plan fetch failed for %s: %s", path, exc)
            text = ""
        return RawArtefact(
            source_uri=candidate.source_uri,
            connector=self.connector,
            tier=self.tier,
            text=text,
            last_modified=candidate.last_modified,
            title_hint=candidate.title_hint,
            metadata=dict(candidate.metadata),
        )

    def identify(self, raw: RawArtefact) -> str:
        """``file://<absolute-path>`` — stable across ingests of the same
        file."""

        return raw.source_uri

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _expand_entry(self, root: Path, entry: str) -> Iterable[Path]:
        """Expand one allowlist entry.

        Three shapes are supported:
        - Plain file path: yield it if it exists under ``root``.
        - Plain directory: walk its tree.
        - Glob pattern: rglob under ``root``.
        """

        if any(ch in entry for ch in "*?["):
            yield from root.glob(entry)
            # Also try rglob for deeper matches.
            yield from root.rglob(entry)
            return
        target = root / entry
        if target.is_dir():
            # Walk the directory tree.
            yield from target.rglob("*")
        else:
            yield target

    def _is_excluded(self, root: Path, path: Path) -> bool:
        """Match ``path`` against the excludelist patterns (glob-style)."""

        try:
            rel = str(path.relative_to(root))
        except ValueError:
            return True
        for pattern in self.excludelist:
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern):
                return True
        return False


def _path_from_uri(source_uri: str) -> Path:
    if source_uri.startswith("file://"):
        return Path(source_uri[len("file://") :])
    return Path(source_uri)
