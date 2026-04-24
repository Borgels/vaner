# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS1 — markdown extractor unit tests."""

from __future__ import annotations

from vaner.intent.artefacts import artefact_id
from vaner.intent.ingest.extract_markdown import (
    extract_markdown,
    normalize_markdown,
)


def test_extracts_headings_as_nested_sections() -> None:
    text = "# Top\n\n## Mid\n\n### Deep\n"
    aid = artefact_id("file:///t.md", "plan")
    result = extract_markdown(artefact_id=aid, kind="plan", text=text)
    kinds = [it.kind for it in result.items]
    assert kinds == ["section", "section", "section"]
    # Deep section carries the full heading path.
    assert result.items[-1].section_path == "Top/Mid/Deep"
    assert result.items[-1].parent_item == result.items[-2].id


def test_extracts_checkbox_states() -> None:
    text = "# Plan\n\n- [ ] open\n- [x] done\n- [-] in_progress\n- [!] stalled\n- [~] contradicted\n"
    aid = artefact_id("file:///t.md", "plan")
    result = extract_markdown(artefact_id=aid, kind="plan", text=text)
    states = [it.state for it in result.items if it.kind == "task"]
    assert states == ["pending", "complete", "in_progress", "stalled", "contradicted"]


def test_stable_item_ids_across_runs() -> None:
    text = "# Plan\n\n- [ ] a\n- [ ] b\n- [x] c\n"
    aid = artefact_id("file:///t.md", "plan")
    r1 = extract_markdown(artefact_id=aid, kind="plan", text=text)
    r2 = extract_markdown(artefact_id=aid, kind="plan", text=text)
    assert [it.id for it in r1.items] == [it.id for it in r2.items]


def test_nested_bullet_parents() -> None:
    text = "# Plan\n\n- [ ] top task\n  - [ ] nested\n  - [x] nested done\n- [ ] sibling\n"
    aid = artefact_id("file:///t.md", "plan")
    result = extract_markdown(artefact_id=aid, kind="plan", text=text)
    tasks = [it for it in result.items if it.kind == "task"]
    assert len(tasks) == 4
    top = tasks[0]
    nested1 = tasks[1]
    nested2 = tasks[2]
    sibling = tasks[3]
    assert nested1.parent_item == top.id
    assert nested2.parent_item == top.id
    assert sibling.parent_item != top.id  # sibling resets nesting


def test_extracts_related_files_from_item_text() -> None:
    text = "# Plan\n\n- [ ] update `src/foo.py`\n- [ ] touch path/to/docs.md\n"
    aid = artefact_id("file:///t.md", "plan")
    result = extract_markdown(artefact_id=aid, kind="plan", text=text)
    tasks = [it for it in result.items if it.kind == "task"]
    assert "src/foo.py" in tasks[0].related_files
    assert "path/to/docs.md" in tasks[1].related_files


def test_normalize_strips_html_comments_and_trailing_ws() -> None:
    text = "# Plan  \n\n<!-- hidden note -->\n\n- [ ] x  \n"
    normalized = normalize_markdown(text)
    assert "<!--" not in normalized
    assert "Plan  " not in normalized
    assert "Plan" in normalized


def test_normalize_collapses_blank_runs_and_is_idempotent() -> None:
    text = "# A\n\n\n\n\n- [ ] one\n\n\n"
    n1 = normalize_markdown(text)
    n2 = normalize_markdown(n1)
    assert n1 == n2
    # Max one blank line between content blocks.
    assert "\n\n\n" not in n1


def test_outline_kind_gives_subgoals_for_bullets() -> None:
    text = "# Outline\n\n## Sec\n\n- point a\n- point b\n"
    aid = artefact_id("file:///t.md", "outline")
    result = extract_markdown(artefact_id=aid, kind="outline", text=text)
    bullet_kinds = [it.kind for it in result.items if it.text.startswith("point")]
    assert bullet_kinds == ["subgoal", "subgoal"]


def test_numbered_list_produces_tasks_for_plan_kind() -> None:
    text = "# Plan\n\n## Phase\n\n1. first\n2. second\n3. third\n"
    aid = artefact_id("file:///t.md", "plan")
    result = extract_markdown(artefact_id=aid, kind="plan", text=text)
    kinds = [it.kind for it in result.items if it.text in ("first", "second", "third")]
    assert kinds == ["task", "task", "task"]
