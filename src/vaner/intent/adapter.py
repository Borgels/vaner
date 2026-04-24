"""Corpus adapter abstractions for Vaner's engine.

Protocols
---------
SignalSource         -- produces context-signal events (file changes, git …)
ContextSource        -- stores retrievable context items with relationships
CorpusAdapter        -- legacy unified protocol (= SignalSource + ContextSource)
IntentArtefactSource -- WS1 0.8.2, produces intent-bearing artefact candidates
                        (plans, outlines, task lists, briefs, …) for the
                        ingestion pipeline to classify + extract + persist

CodeRepoAdapter implements the first three for backward compatibility.
Third-party adapters only need to implement the two focused protocols for
their role.

IntentArtefactSource is separate from SignalSource because intent-bearing
artefacts are discovered and fetched on their own cadence (opt-in per tier,
rate-limited for remote sources, classifier-gated) rather than streamed as
raw mutations. Connectors that carry *both* roles (e.g. a local-plan
connector that also emits ``file_seen`` signals when a plan is saved) simply
implement both protocols.
"""

# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from vaner.daemon.signals.fs_watcher import scan_repo_files
from vaner.daemon.signals.git_reader import read_git_state
from vaner.intent.artefacts import SourceTier
from vaner.intent.skills_discovery import discover_skills
from vaner.models.signal import SignalEvent


@dataclass(slots=True)
class CorpusItem:
    key: str
    content: str
    metadata: dict[str, str] = field(default_factory=dict)
    updated_at: float = 0.0
    corpus_id: str = "default"
    privacy_zone: str = "local"


@dataclass(slots=True)
class MutationEvent:
    source: str
    kind: str
    payload: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    corpus_id: str = "default"
    privacy_zone: str = "local"


@dataclass(slots=True)
class RelationshipEdge:
    source_key: str
    target_key: str
    kind: str
    corpus_id: str = "default"


@dataclass(slots=True)
class QualityIssue:
    key: str
    severity: str
    message: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ReasonerContext:
    corpus_type: str
    summary: str
    metadata: dict[str, str] = field(default_factory=dict)
    corpus_id: str = "default"
    privacy_zone: str = "local"


class SignalSource(Protocol):
    """Anything that produces context-signal events about the user's state.

    Examples: file-system watcher, git poller, IDE open-file tracker,
    calendar integration, browser history.
    """

    source_type: str

    async def collect(self) -> list[SignalEvent]:
        """Return recent signal events since the last call."""
        ...

    async def detect_mutations(self, since: float) -> list[MutationEvent]:
        """Return mutation events observed after *since* (epoch seconds)."""
        ...


class ContextSource(Protocol):
    """Anything that stores retrievable context items with a relationship graph.

    Examples: code repository files, document store, knowledge-base articles.
    """

    source_type: str

    async def list_items(self, limit: int = 500) -> list[CorpusItem]:
        """Enumerate available context items."""
        ...

    async def get_item(self, key: str) -> CorpusItem:
        """Fetch a single context item by key."""
        ...

    async def extract_relationships(self) -> list[RelationshipEdge]:
        """Return edges describing relationships between context items."""
        ...

    async def check_quality(self) -> list[QualityIssue]:
        """Return quality issues found in the context corpus."""
        ...

    async def get_context_for_reasoning(self) -> ReasonerContext:
        """Return a summary of the current context state for LLM reasoning."""
        ...


class CorpusAdapter(Protocol):
    """Legacy unified protocol -- implements both SignalSource and ContextSource.

    Kept for backward compatibility.  Prefer the focused protocols for new code.
    """

    corpus_type: str

    async def list_items(self) -> list[CorpusItem]: ...

    async def get_item(self, key: str) -> CorpusItem: ...

    async def detect_mutations(self, since: float) -> list[MutationEvent]: ...

    async def extract_relationships(self) -> list[RelationshipEdge]: ...

    async def check_quality(self) -> list[QualityIssue]: ...

    async def get_context_for_reasoning(self) -> ReasonerContext: ...


class CodeRepoAdapter:
    """Default corpus adapter for code repositories.

    Implements CorpusAdapter (legacy), SignalSource, and ContextSource so it
    can be passed to any of the three protocol slots.
    """

    corpus_type = "code_repo"
    source_type = "code_repo"
    corpus_id = "repo"
    privacy_zone = "project_local"

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self._last_collect_time: float = 0.0

    async def collect(self) -> list[SignalEvent]:
        """Collect file-change and git-state signal events since last call."""
        now = time.time()
        mutations = await self.detect_mutations(self._last_collect_time)
        self._last_collect_time = now
        return [self.to_signal(m) for m in mutations]

    async def list_items(self, limit: int = 500) -> list[CorpusItem]:
        items: list[CorpusItem] = []
        for path in scan_repo_files(self.repo_root, max_files=limit):
            rel = str(path.relative_to(self.repo_root))
            items.append(
                CorpusItem(
                    key=f"file:{rel}",
                    content=rel,
                    metadata={"path": rel, "corpus_id": self.corpus_id, "privacy_zone": self.privacy_zone},
                    updated_at=path.stat().st_mtime,
                    corpus_id=self.corpus_id,
                    privacy_zone=self.privacy_zone,
                )
            )
        return items

    async def get_item(self, key: str) -> CorpusItem:
        if not key.startswith("file:"):
            raise KeyError(f"Unsupported key: {key}")
        rel = key.split(":", 1)[1]
        path = (self.repo_root / rel).resolve()
        try:
            path.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValueError(f"Path escapes repository root: {rel}") from exc
        text = ""
        if path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
        return CorpusItem(
            key=key,
            content=text,
            metadata={"path": rel, "corpus_id": self.corpus_id, "privacy_zone": self.privacy_zone},
            updated_at=path.stat().st_mtime if path.exists() else 0.0,
            corpus_id=self.corpus_id,
            privacy_zone=self.privacy_zone,
        )

    async def detect_mutations(self, since: float) -> list[MutationEvent]:
        events: list[MutationEvent] = []
        for path in scan_repo_files(self.repo_root, max_files=500):
            stat = path.stat()
            if stat.st_mtime < since:
                continue
            rel = str(path.relative_to(self.repo_root))
            events.append(
                MutationEvent(
                    source="fs_scan",
                    kind="file_seen",
                    payload={"path": rel},
                    timestamp=stat.st_mtime,
                    corpus_id=self.corpus_id,
                    privacy_zone=self.privacy_zone,
                )
            )

        git_state = read_git_state(self.repo_root)
        for line in (git_state.get("recent_diff", "") + "\n" + git_state.get("staged", "")).splitlines():
            rel = line.strip()
            if not rel:
                continue
            events.append(
                MutationEvent(
                    source="git",
                    kind="git_changed",
                    payload={"path": rel},
                    timestamp=time.time(),
                    corpus_id=self.corpus_id,
                    privacy_zone=self.privacy_zone,
                )
            )
        return events

    async def extract_relationships(self) -> list[RelationshipEdge]:
        from vaner.intent.graph import extract_code_relationship_edges

        return extract_code_relationship_edges(self.repo_root)

    async def check_quality(self) -> list[QualityIssue]:
        from vaner.intent.quality import run_code_quality_scan

        return run_code_quality_scan(self.repo_root)

    async def get_context_for_reasoning(self) -> ReasonerContext:
        git_state = read_git_state(self.repo_root)
        skills = discover_skills(self.repo_root, include_global=False)
        summary = (
            f"branch={git_state.get('branch', '')}\nrecent_diff={git_state.get('recent_diff', '')}\nstaged={git_state.get('staged', '')}\n"
        )
        return ReasonerContext(
            corpus_type=self.corpus_type,
            summary=summary,
            metadata={
                "repo_root": str(self.repo_root),
                "corpus_id": self.corpus_id,
                "privacy_zone": self.privacy_zone,
                "skills": ",".join(skill.name for skill in skills[:20]),
                "skill_kinds": ",".join(skill.vaner_kind for skill in skills[:20] if skill.vaner_kind),
            },
            corpus_id=self.corpus_id,
            privacy_zone=self.privacy_zone,
        )

    @staticmethod
    def to_signal(event: MutationEvent) -> SignalEvent:
        payload = dict(event.payload)
        payload.setdefault("corpus_id", event.corpus_id)
        payload.setdefault("privacy_zone", event.privacy_zone)
        return SignalEvent(
            id=str(uuid.uuid4()),
            source=event.source,
            kind=event.kind,
            timestamp=event.timestamp,
            payload=payload,
        )


# --------------------------------------------------------------------------
# 0.8.2 WS1 — IntentArtefactSource protocol
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ArtefactCandidate:
    """Lightweight discovery result from an :class:`IntentArtefactSource`.

    Carries just enough to decide whether to fetch: a stable ``source_uri``
    (the identity handle), ``connector`` name, optional ``hint_kind`` if the
    source can pre-guess the artefact kind, and a cheap
    ``last_modified`` timestamp used to skip fetches on unchanged sources.
    Full content is deferred to :meth:`IntentArtefactSource.fetch` so
    discovery passes stay cheap on large source trees.
    """

    source_uri: str
    connector: str
    tier: SourceTier
    hint_kind: str | None = None
    last_modified: float = 0.0
    title_hint: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RawArtefact:
    """Fetched artefact content before classification / extraction.

    The pipeline classifier operates on ``text`` + ``metadata``; the
    extractor operates on ``text`` + ``hint_kind``. Connectors are the only
    code path that talks to external systems; downstream stages treat
    ``RawArtefact`` as a pure value.
    """

    source_uri: str
    connector: str
    tier: SourceTier
    text: str
    last_modified: float
    hint_kind: str | None = None
    title_hint: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


DiscoveryMode = Literal["poll", "signal"]


class IntentArtefactSource(Protocol):
    """Source of intent-bearing artefact candidates (plans, outlines, task
    lists, briefs, roadmaps, runbooks).

    Separate from :class:`SignalSource` because artefacts are classified +
    extracted + persisted on their own cadence rather than streamed as raw
    mutations. A connector carrying both roles (e.g. a local-plan watcher
    that also emits ``file_seen`` signals) simply implements both protocols.

    Implementation contract
    -----------------------
    - ``tier`` — source trust tier (T1 auto-enabled; T2+ opt-in).
    - ``connector`` — stable short name (``local_plan``, ``markdown_outline``,
      ``github_issues``, …) that appears in the store row and MCP responses.
    - ``discover()`` — enumerate candidate sources cheaply. Must be safe to
      call on every cycle; expensive work (fetching content, API calls with
      body bytes) belongs in ``fetch``.
    - ``fetch(candidate)`` — materialize the raw content. May be async-blocking
      on network I/O; the pipeline rate-limits the call sites.
    - ``identify(raw)`` — return a stable ``source_uri`` for the raw artefact.
      Used as the artefact's identity key across snapshots. Must be
      deterministic for a given logical source (same file path, same issue
      id, same doc id) so revisions produce a new *snapshot* rather than a
      new *artefact*.
    """

    tier: SourceTier
    connector: str

    async def discover(self) -> Iterable[ArtefactCandidate]:
        """Enumerate artefact candidates from this source. Cheap — no body
        fetches, no LLM calls. Must respect the tier / allowlist config
        (the pipeline will *not* filter a second time)."""

    async def fetch(self, candidate: ArtefactCandidate) -> RawArtefact:
        """Fetch the raw content for a candidate. May perform network I/O."""

    def identify(self, raw: RawArtefact) -> str:
        """Return the stable ``source_uri`` for this artefact. Deterministic
        across runs; same logical source → same uri."""
