# SPDX-License-Identifier: Apache-2.0
"""WS1 — ingestion pipeline orchestrator.

Stateless top-level function :func:`ingest_artefact` that runs the full
pipeline for one :class:`RawArtefact`: classify, dispatch to the right
extractor, compute snapshot id, link to any prior snapshot, and persist
artefact + snapshot + items. Emits an ``artefact_seen`` invalidation
signal so downstream goal inference (WS2) and reconciliation (WS3) can
respond without polling.

The function has no side effects beyond the injected :class:`ArtefactStore`
and the signal emission. It does not call connectors directly — the
caller passes the already-fetched :class:`RawArtefact`. This keeps the
ingestion kernel testable as a pure unit.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field

from vaner.intent.adapter import RawArtefact
from vaner.intent.artefacts import (
    IntentArtefact,
    IntentArtefactItem,
    IntentArtefactSnapshot,
)
from vaner.intent.artefacts import (
    artefact_id as artefact_id_from,
)
from vaner.intent.ingest.classifier import (
    ClassificationResult,
    ClassifierLLMCallable,
    classify,
)
from vaner.intent.ingest.extract_github import extract_github
from vaner.intent.ingest.extract_markdown import extract_markdown
from vaner.models.signal import SignalEvent
from vaner.store.artefacts import ArtefactStore


@dataclass(slots=True)
class IngestResult:
    """Per-artefact pipeline outcome.

    - ``accepted`` — classifier verdict; ``False`` means nothing was
      persisted (artefact rows, snapshots, items, signals are all skipped).
    - ``classification`` — always populated so callers can log
      near-misses and debug the classifier's reasoning.
    - ``artefact`` / ``snapshot`` / ``items`` — populated on accept.
    - ``is_new_snapshot`` — ``False`` when the incoming content matches
      the artefact's ``latest_snapshot`` exactly (no new row written).
      ``True`` on first ingest or when the content changed.
    - ``emitted_signal_id`` — id of the ``artefact_seen`` signal event
      emitted on accept. ``None`` when nothing was emitted (reject path,
      or a no-op re-ingest where nothing changed).
    """

    accepted: bool
    classification: ClassificationResult
    artefact: IntentArtefact | None = None
    snapshot: IntentArtefactSnapshot | None = None
    items: list[IntentArtefactItem] = field(default_factory=list)
    is_new_snapshot: bool = False
    emitted_signal_id: str | None = None


def _dispatch_extract(
    *,
    connector: str,
    artefact_id: str,
    kind: str,
    raw: RawArtefact,
):
    """Pick the right extractor for ``connector``.

    WS1 ships markdown + github. A future board extractor (WS4) will plug
    in here by connector name (``"github_projects"``, ``"local_board"``).
    """

    if connector == "github_issues":
        return extract_github(artefact_id=artefact_id, kind=kind, raw=raw)
    return extract_markdown(artefact_id=artefact_id, kind=kind, text=raw.text or "")


def _snapshot_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def ingest_artefact(
    raw: RawArtefact,
    *,
    store: ArtefactStore,
    llm: ClassifierLLMCallable | None = None,
    emit_signal: bool = True,
) -> IngestResult:
    """Run the full ingestion pipeline for one raw artefact.

    Classify → dispatch extract → compute snapshot id → persist
    artefact + snapshot + items → emit ``artefact_seen`` signal.

    Returns an :class:`IngestResult` even on reject so the caller can
    log classifier near-misses; only accepted records produce persisted
    state and signals.

    ``emit_signal`` can be disabled for batch backfills that want to
    suppress signal storms; the default (enabled) is what the daemon
    uses.
    """

    classification = await classify(raw, llm=llm)
    if not classification.is_intent_bearing or classification.kind is None:
        return IngestResult(accepted=False, classification=classification)

    kind = classification.kind
    art_id = artefact_id_from(raw.source_uri, kind)

    extraction = _dispatch_extract(
        connector=raw.connector,
        artefact_id=art_id,
        kind=kind,
        raw=raw,
    )
    snapshot_content_hash = _snapshot_hash(extraction.normalized_text)
    snap_id = snapshot_content_hash

    prior_row = await store.get_intent_artefact(art_id)
    now = time.time()
    is_new_snapshot = prior_row is None or str(prior_row.get("latest_snapshot") or "") != snap_id

    # Prefer the artefact's first H1 heading as the human-readable title
    # when the extractor found one — the filename (``title_hint``) is a
    # fallback. WS2 goal inference shows the title to the user via
    # ``vaner.goals.list``, so a meaningful heading beats ``plan.md``.
    extracted_title = _first_heading_title(extraction.items)
    preferred_title = extracted_title or raw.title_hint or raw.source_uri

    if prior_row is None:
        artefact = IntentArtefact.new(
            source_uri=raw.source_uri,
            source_tier=raw.tier,
            connector=raw.connector,
            kind=kind,
            title=preferred_title,
            confidence=classification.confidence,
        )
        artefact.id = art_id  # paranoia: align with deterministic id
        artefact.created_at = now
        artefact.last_observed_at = now
        artefact.latest_snapshot = snap_id
        artefact.linked_files = _collect_linked_files(extraction.items)
    else:
        artefact = IntentArtefact(
            id=art_id,
            source_uri=str(prior_row["source_uri"]),
            source_tier=str(prior_row["source_tier"]),  # type: ignore[arg-type]
            connector=str(prior_row["connector"]),
            kind=kind,
            title=preferred_title or str(prior_row.get("title") or ""),
            status=str(prior_row.get("status") or "active"),  # type: ignore[arg-type]
            confidence=classification.confidence,
            created_at=float(prior_row.get("created_at") or now),
            last_observed_at=now,
            last_reconciled_at=(float(prior_row["last_reconciled_at"]) if prior_row.get("last_reconciled_at") is not None else None),
            latest_snapshot=snap_id,
            linked_goals=_load_json_list(prior_row.get("linked_goals_json")),
            linked_files=_collect_linked_files(extraction.items),
            supersedes=(str(prior_row["supersedes"]) if prior_row.get("supersedes") is not None else None),
        )

    snapshot = IntentArtefactSnapshot(
        id=snap_id,
        artefact_id=art_id,
        captured_at=now,
        content_hash=snapshot_content_hash,
        text=extraction.normalized_text,
        items=list(extraction.items),
    )

    # Persist.
    await store.upsert_intent_artefact(
        id=artefact.id,
        source_uri=artefact.source_uri,
        source_tier=artefact.source_tier,
        connector=artefact.connector,
        kind=artefact.kind,
        title=artefact.title,
        status=artefact.status,
        confidence=artefact.confidence,
        created_at=artefact.created_at,
        last_observed_at=artefact.last_observed_at,
        last_reconciled_at=artefact.last_reconciled_at,
        latest_snapshot=artefact.latest_snapshot,
        linked_goals_json=json.dumps(artefact.linked_goals),
        linked_files_json=json.dumps(artefact.linked_files),
        supersedes=artefact.supersedes,
    )
    if is_new_snapshot:
        await store.upsert_intent_artefact_snapshot(
            id=snapshot.id,
            artefact_id=snapshot.artefact_id,
            captured_at=snapshot.captured_at,
            content_hash=snapshot.content_hash,
            text=snapshot.text,
        )
        await store.replace_intent_artefact_items(
            snapshot_id=snapshot.id,
            artefact_id=artefact.id,
            items=[
                {
                    "id": it.id,
                    "text": it.text,
                    "kind": it.kind,
                    "state": it.state,
                    "section_path": it.section_path,
                    "parent_item": it.parent_item,
                    "related_files_json": json.dumps(it.related_files),
                    "related_entities_json": json.dumps(it.related_entities),
                    "evidence_refs_json": json.dumps(it.evidence_refs),
                }
                for it in snapshot.items
            ],
        )

    signal_id: str | None = None
    if emit_signal and is_new_snapshot:
        signal_id = str(uuid.uuid4())
        await store.insert_signal_event(
            SignalEvent(
                id=signal_id,
                source=f"intent_artefact:{artefact.connector}",
                kind="artefact_seen",
                timestamp=now,
                payload={
                    "artefact_id": artefact.id,
                    "snapshot_id": snapshot.id,
                    "kind": kind,
                    "confidence": classification.confidence,
                    "tier": artefact.source_tier,
                    "supersedes_snapshot": (str(prior_row["latest_snapshot"]) if prior_row and prior_row.get("latest_snapshot") else None),
                },
            )
        )

    return IngestResult(
        accepted=True,
        classification=classification,
        artefact=artefact,
        snapshot=snapshot,
        items=list(snapshot.items),
        is_new_snapshot=is_new_snapshot,
        emitted_signal_id=signal_id,
    )


def _first_heading_title(items: list[IntentArtefactItem]) -> str | None:
    """Return the text of the earliest top-level section item, if any.

    "Top-level" = an extractor-produced section whose ``section_path``
    has exactly one segment (the extractor sets this to the heading text
    itself for an H1). Used to give :class:`IntentArtefact` records
    readable titles when the connector only supplied a filename hint.
    """

    for item in items:
        if item.kind == "section" and item.section_path.count("/") == 0 and item.text:
            return item.text.strip() or None
    return None


def _collect_linked_files(items: list[IntentArtefactItem]) -> list[str]:
    """De-duplicated ordered list of file paths referenced by any item."""

    seen: list[str] = []
    for it in items:
        for path in it.related_files:
            if path and path not in seen:
                seen.append(path)
    return seen


def _load_json_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    return []
