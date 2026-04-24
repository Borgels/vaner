# SPDX-License-Identifier: Apache-2.0
"""WS1 — MarkdownOutlineAdapter (T2, opt-in).

Broader scan than :class:`LocalPlanAdapter`: walks the whole configured
workspace allowlist and yields every markdown file as a candidate, not
just files under the plan folders. Because this has much higher
false-positive potential (most markdown is README / docs / notes), the
adapter is T2 and the classifier carries the real filtering load.

Default allowlist: the workspace root, subject to the default
excludelist plus a broad set of noise directories (``node_modules``,
``.git``, ``venv``, ``dist``, ``build``, ``__pycache__``). Users can
customise both via ``config.sources.intent_artefacts.markdown_outline``.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from vaner.intent.adapter import ArtefactCandidate, RawArtefact
from vaner.intent.artefacts import SourceTier
from vaner.intent.connectors.local_plan import (
    _MAX_FILE_BYTES,
    _MIN_FILE_BYTES,
    DEFAULT_EXCLUDELIST,
    _path_from_uri,
)

_MARKDOWN_EXTS: frozenset[str] = frozenset({".md", ".markdown", ".mdx"})

# Directories we never descend into when scanning for plans — they almost
# never contain intent-bearing artefacts and the noise degrades classifier
# precision. The user's excludelist stacks on top of this.
_DEFAULT_NOISE_DIRS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "target",
    "site-packages",
)


@dataclass(slots=True)
class MarkdownOutlineAdapter:
    """T2 (opt-in) connector for workspace-wide markdown scanning."""

    workspace_root: Path
    excludelist: tuple[str, ...] = field(default=DEFAULT_EXCLUDELIST)
    noise_dirs: tuple[str, ...] = field(default=_DEFAULT_NOISE_DIRS)
    connector: str = "markdown_outline"
    tier: SourceTier = "T2"
    max_candidates: int = 500

    async def discover(self) -> Iterable[ArtefactCandidate]:
        root = self.workspace_root.resolve()
        candidates: list[ArtefactCandidate] = []
        noise = set(self.noise_dirs)
        for path in self._walk(root, noise):
            if len(candidates) >= self.max_candidates:
                break
            if not path.is_file():
                continue
            if path.suffix.lower() not in _MARKDOWN_EXTS:
                continue
            if self._is_excluded(root, path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size < _MIN_FILE_BYTES or stat.st_size > _MAX_FILE_BYTES:
                continue
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                continue
            candidates.append(
                ArtefactCandidate(
                    source_uri=f"file://{path}",
                    connector=self.connector,
                    tier=self.tier,
                    last_modified=stat.st_mtime,
                    title_hint=path.name,
                    metadata={"workspace_rel_path": rel},
                )
            )
        return candidates

    async def fetch(self, candidate: ArtefactCandidate) -> RawArtefact:
        path = _path_from_uri(candidate.source_uri)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
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
        return raw.source_uri

    def _walk(self, root: Path, noise: set[str]) -> Iterable[Path]:
        """Depth-first walk with noise-dir pruning."""

        stack = [root]
        while stack:
            cur = stack.pop()
            try:
                entries = list(cur.iterdir())
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir():
                        if entry.name in noise:
                            continue
                        stack.append(entry)
                    elif entry.is_file():
                        yield entry
                except OSError:
                    continue

    def _is_excluded(self, root: Path, path: Path) -> bool:
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            return True
        for pattern in self.excludelist:
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern):
                return True
        return False
