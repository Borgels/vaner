# SPDX-License-Identifier: Apache-2.0
"""WS1 — GitHub issues extractor.

Flattens a GitHub issue (or milestone-summary artefact) into
:class:`IntentArtefactItem` records. Delegates the body-markdown parsing
to :mod:`vaner.intent.ingest.extract_markdown` — issues are just markdown
with extra metadata around them — and adds:

- A synthetic "Title" section item prepended to the items list so the
  issue title is explicit in the item graph.
- Issue state → state on the root item: ``open`` → ``pending``,
  ``closed`` → ``complete``. Body-level checkboxes retain their own
  per-task state.
- Labels → ``related_entities`` on the root item. Assignees are captured
  in ``evidence_refs`` as ``assignee:<login>`` markers so later
  goal-inference can correlate who-owns-what if it cares.

Metadata contract: the caller puts issue-shaped fields on
``raw.metadata`` — ``issue_number``, ``issue_state`` (``open`` /
``closed``), ``issue_labels`` (comma-joined), ``issue_assignees``
(comma-joined), ``repo`` (``owner/name``). The connector owns the
conversion from GitHub JSON to this shape; the extractor never touches
the API.
"""

from __future__ import annotations

from vaner.intent.adapter import RawArtefact
from vaner.intent.artefacts import (
    IntentArtefactItem,
    IntentArtefactKind,
    artefact_item_id,
)
from vaner.intent.ingest.extract_markdown import (
    ExtractionResult,
    extract_markdown,
    normalize_markdown,
)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [tok.strip() for tok in value.split(",") if tok.strip()]


def extract_github(
    *,
    artefact_id: str,
    kind: IntentArtefactKind,
    raw: RawArtefact,
) -> ExtractionResult:
    """Extract items from a GitHub issue or milestone-summary artefact.

    Prepends a synthetic root section item that carries the issue title,
    state, labels, and assignees so reconciliation (WS3) can read the
    root's evidence refs directly without reparsing markdown. Body items
    come from the standard markdown extractor and inherit the root as
    their section path ancestor.
    """

    issue_state = (raw.metadata.get("issue_state") or "open").strip().lower()
    root_state = "complete" if issue_state == "closed" else "pending"
    labels = _split_csv(raw.metadata.get("issue_labels"))
    assignees = _split_csv(raw.metadata.get("issue_assignees"))
    repo = raw.metadata.get("repo", "")
    issue_number = raw.metadata.get("issue_number", "")

    title = (raw.title_hint or "").strip() or (f"{repo}#{issue_number}".strip("#") if issue_number else raw.source_uri)

    root_id = artefact_item_id(artefact_id, "", title)
    evidence_refs = [f"assignee:{a}" for a in assignees]
    if issue_number:
        evidence_refs.insert(0, f"issue:{repo}#{issue_number}" if repo else f"issue:#{issue_number}")

    root_item = IntentArtefactItem(
        id=root_id,
        artefact_id=artefact_id,
        text=title,
        kind="section",
        state=root_state,  # type: ignore[arg-type]
        section_path="",
        parent_item=None,
        related_files=[],
        related_entities=list(labels),
        evidence_refs=evidence_refs,
    )

    # Delegate body markdown parsing. We reparent the extracted items so
    # their section_path is prefixed with the title, and top-level items
    # get the root as their parent.
    body_result = extract_markdown(artefact_id=artefact_id, kind=kind, text=raw.text or "")
    reparented: list[IntentArtefactItem] = []
    for item in body_result.items:
        new_section_path = f"{title}/{item.section_path}" if item.section_path else title
        parent = item.parent_item or root_id
        # Recompute id so section_path participates in identity.
        new_id = artefact_item_id(artefact_id, new_section_path, item.text)
        reparented.append(
            IntentArtefactItem(
                id=new_id,
                artefact_id=artefact_id,
                text=item.text,
                kind=item.kind,
                state=item.state,
                section_path=new_section_path,
                parent_item=parent,
                related_files=list(item.related_files),
                related_entities=list(item.related_entities),
                evidence_refs=list(item.evidence_refs),
            )
        )

    normalized = _normalize_issue(title=title, body=body_result.normalized_text)

    items = [root_item, *reparented]
    return ExtractionResult(items=items, normalized_text=normalized)


def _normalize_issue(*, title: str, body: str) -> str:
    """Canonical form for an issue artefact.

    Title + body so title-only changes (a retitled issue) produce a new
    snapshot even when the body is unchanged. Reuses the markdown
    normalization for the body portion so round-trips are stable.
    """

    body = normalize_markdown(body)
    if not title:
        return body
    return f"# {title}\n\n{body}"
