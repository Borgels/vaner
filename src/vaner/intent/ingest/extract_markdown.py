# SPDX-License-Identifier: Apache-2.0
"""WS1 — markdown extractor for intent-bearing artefacts.

Takes a markdown :class:`RawArtefact` that has been classified positive
and emits a list of :class:`IntentArtefactItem` records plus the
normalized text that the pipeline hashes to form the snapshot id.

Scope in WS1: checkboxes (GFM task syntax), headings (ATX ``#`` style up
to depth 6), ordered and unordered lists, nested bullet hierarchy, inline
code references that look like file paths. Non-goals for WS1: GFM tables,
HTML blocks, reference-style links, setext headings (underlined h1/h2) —
these are rare in practice for plan artefacts and can be added later if
the fixture corpus shows demand.

The extractor is pure: same text → same items → same item ids. This
matters for reconciliation — WS3 compares item id sets across snapshots
to infer checkbox flips, new tasks, and removed sections, so stable ids
are a correctness requirement, not just a convenience.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from vaner.intent.artefacts import (
    IntentArtefactItem,
    IntentArtefactKind,
    ItemKind,
    ItemState,
    artefact_item_id,
)

# --------------------------------------------------------------------------
# Token regex
# --------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*#*\s*$")
# Checkbox: ``- [ ] task``, ``- [x] done``, ``* [X] done``, ``+ [-] in_progress``,
# ``- [?] unknown``. Accepts ``x``/``X``/space for GFM core plus common
# extensions (``-`` in_progress, ``/`` in_progress, ``!`` blocked, ``?`` note).
_CHECKBOX_RE = re.compile(r"^(\s*)([-*+])\s+\[([ xX\-/!\?~])\]\s+(.*\S)\s*$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*\S)\s*$")
_UNORDERED_RE = re.compile(r"^(\s*)([-*+])\s+(?!\[[ xX\-/!\?~]\])(.*\S)\s*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)
# Inline code references that look like file paths — used to populate
# ``related_files`` on items.  Accepts ``src/foo.py`` or ``path/to/thing.md``
# inside backticks.
_FILE_PATH_RE = re.compile(r"`([A-Za-z0-9_./\-]+\.[A-Za-z0-9_]+)`")
# Bare file paths in prose — more conservative, must contain a slash and
# end in a known file extension class.
_BARE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.[A-Za-z0-9_]{1,5})")

_CHECKBOX_STATE: dict[str, ItemState] = {
    " ": "pending",
    "x": "complete",
    "X": "complete",
    "-": "in_progress",
    "/": "in_progress",
    "!": "stalled",
    "?": "pending",
    "~": "contradicted",
}


@dataclass(slots=True)
class ExtractionResult:
    """Pipeline-facing result of one extraction pass.

    ``items`` carries every item the extractor saw — tasks, headings, list
    bullets, notes. ``normalized_text`` is the stable canonical form of
    the artefact that the pipeline hashes to form the snapshot id.
    """

    items: list[IntentArtefactItem] = field(default_factory=list)
    normalized_text: str = ""


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


def normalize_markdown(text: str) -> str:
    """Produce a stable canonical form of the markdown.

    - Strip HTML comments (reconciliation shouldn't flip on editorial notes).
    - Normalize line endings to ``\\n``.
    - Trim trailing whitespace on every line.
    - Collapse runs of blank lines to a single blank line.
    - Trim leading/trailing blank lines from the whole document.
    """

    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = _HTML_COMMENT_RE.sub("", t)
    t = _TRAILING_WS_RE.sub("", t)
    lines = t.split("\n")
    # Collapse multi-blank runs.
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 1:
                out.append("")
        else:
            blank_run = 0
            out.append(line)
    # Trim leading / trailing blanks.
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + ("\n" if out else "")


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def _extract_related_files(text: str) -> list[str]:
    hits: list[str] = []
    for match in _FILE_PATH_RE.finditer(text):
        candidate = match.group(1)
        if candidate not in hits:
            hits.append(candidate)
    for match in _BARE_PATH_RE.finditer(text):
        candidate = match.group(1)
        # Strip trailing punctuation that might have attached.
        candidate = candidate.rstrip(".,);:")
        if candidate not in hits:
            hits.append(candidate)
    return hits


def _heading_section_path(stack: list[tuple[int, str]]) -> str:
    """Render the current heading stack as a ``/``-separated section path."""

    return "/".join(title for _level, title in stack)


def _choose_item_kind(kind: IntentArtefactKind, is_checkbox: bool) -> ItemKind:
    """Pick the :class:`ItemKind` for a content line given the artefact
    kind. Checkboxes are always ``task``; other bullets vary by artefact
    kind.
    """

    if is_checkbox:
        return "task"
    if kind == "outline":
        return "subgoal"
    if kind in ("plan", "runbook", "checklist", "task_list", "queue"):
        return "task"
    # ``brief`` is narrative; list items are supporting notes, not work items.
    return "note"


def extract_markdown(
    *,
    artefact_id: str,
    kind: IntentArtefactKind,
    text: str,
) -> ExtractionResult:
    """Extract items + normalized text from a markdown artefact.

    ``artefact_id`` is used as the prefix for stable item ids via
    :func:`artefact_item_id`. ``kind`` influences the :class:`ItemKind`
    chosen for non-checkbox bullets — outline bullets become
    ``subgoal`` while runbook steps stay ``task``.
    """

    normalized = normalize_markdown(text)
    items: list[IntentArtefactItem] = []
    heading_stack: list[tuple[int, str]] = []
    # Parent tracking for list items: (indent_depth → last item id at that
    # depth). Lets nested bullets inherit the right parent.
    indent_parents: dict[int, str] = {}

    for raw_line in normalized.split("\n"):
        if not raw_line.strip():
            # Blank line ends any list indentation context.
            indent_parents.clear()
            continue

        # Heading?
        heading_match = _HEADING_RE.match(raw_line)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            # Pop the stack until we're at depth < current level.
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            indent_parents.clear()
            section_path = _heading_section_path(heading_stack)
            # Parent is the next-higher heading if any.
            parent_section_id: str | None = None
            if len(heading_stack) > 1:
                parent_path = _heading_section_path(heading_stack[:-1])
                parent_section_id = artefact_item_id(artefact_id, parent_path, heading_stack[-2][1])
            item = IntentArtefactItem.new(
                artefact_id=artefact_id,
                text=title,
                kind="section",
                section_path=section_path,
                state="pending",
                parent_item=parent_section_id,
                related_files=_extract_related_files(title),
            )
            items.append(item)
            continue

        section_path = _heading_section_path(heading_stack)

        # Checkbox?
        checkbox_match = _CHECKBOX_RE.match(raw_line)
        if checkbox_match:
            indent = len(checkbox_match.group(1))
            state_char = checkbox_match.group(3)
            body = checkbox_match.group(4).strip()
            state: ItemState = _CHECKBOX_STATE.get(state_char, "pending")
            parent = _parent_for_indent(indent, indent_parents, heading_stack, artefact_id)
            item = IntentArtefactItem.new(
                artefact_id=artefact_id,
                text=body,
                kind=_choose_item_kind(kind, is_checkbox=True),
                section_path=section_path,
                state=state,
                parent_item=parent,
                related_files=_extract_related_files(body),
            )
            items.append(item)
            indent_parents[indent] = item.id
            continue

        # Ordered list item?
        ordered_match = _ORDERED_RE.match(raw_line)
        if ordered_match:
            indent = len(ordered_match.group(1))
            body = ordered_match.group(3).strip()
            parent = _parent_for_indent(indent, indent_parents, heading_stack, artefact_id)
            item = IntentArtefactItem.new(
                artefact_id=artefact_id,
                text=body,
                kind=_choose_item_kind(kind, is_checkbox=False),
                section_path=section_path,
                state="pending",
                parent_item=parent,
                related_files=_extract_related_files(body),
            )
            items.append(item)
            indent_parents[indent] = item.id
            continue

        # Unordered list item (not a checkbox — the checkbox regex
        # already consumed those).
        unordered_match = _UNORDERED_RE.match(raw_line)
        if unordered_match:
            indent = len(unordered_match.group(1))
            body = unordered_match.group(3).strip()
            parent = _parent_for_indent(indent, indent_parents, heading_stack, artefact_id)
            item_kind = _choose_item_kind(kind, is_checkbox=False)
            item = IntentArtefactItem.new(
                artefact_id=artefact_id,
                text=body,
                kind=item_kind,
                section_path=section_path,
                state="pending",
                parent_item=parent,
                related_files=_extract_related_files(body),
            )
            items.append(item)
            indent_parents[indent] = item.id
            continue

    return ExtractionResult(items=items, normalized_text=normalized)


def _parent_for_indent(
    indent: int,
    indent_parents: dict[int, str],
    heading_stack: list[tuple[int, str]],
    artefact_id: str,
) -> str | None:
    """Return the parent item id for a list item at ``indent`` depth.

    Walks the nearest shallower indent seen; falls back to the current
    heading as the parent section when no shallower list exists.
    """

    # Prune any equal-or-deeper entries — new item at this depth resets
    # them.
    for key in sorted(indent_parents.keys()):
        if key >= indent:
            indent_parents.pop(key, None)
    if indent_parents:
        shallowest = max(k for k in indent_parents if k < indent)
        return indent_parents[shallowest]
    if heading_stack:
        section_path = _heading_section_path(heading_stack)
        return artefact_item_id(artefact_id, section_path, heading_stack[-1][1])
    return None
