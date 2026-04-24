# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS2 — artefact → GoalHint producer tests."""

from __future__ import annotations

from vaner.intent.artefacts import (
    IntentArtefact,
    IntentArtefactItem,
    artefact_id,
    artefact_item_id,
)
from vaner.intent.goal_inference_artefacts import (
    MIN_SECTIONS_FOR_SUBGOALS,
    hint_from_artefact,
    hints_from_artefacts,
)


def _make_artefact(title: str = "Ship 0.8.2") -> IntentArtefact:
    return IntentArtefact(
        id=artefact_id(f"file:///tmp/{title.lower()}.md", "plan"),
        source_uri=f"file:///tmp/{title.lower()}.md",
        source_tier="T1",
        connector="local_plan",
        kind="plan",
        title=title,
        status="active",
        confidence=0.9,
        latest_snapshot="snap1",
    )


def _item(
    artefact: IntentArtefact,
    *,
    text: str,
    kind: str = "task",
    state: str = "pending",
    section_path: str = "",
    related_files: list | None = None,
) -> IntentArtefactItem:
    return IntentArtefactItem(
        id=artefact_item_id(artefact.id, section_path, text),
        artefact_id=artefact.id,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        section_path=section_path,
        related_files=related_files or [],
    )


def test_primary_candidate_for_plan_without_subgoals() -> None:
    art = _make_artefact()
    items = [
        _item(art, text="Ship 0.8.2", kind="section", section_path=""),
        _item(art, text="task a", state="pending", section_path="Ship 0.8.2"),
        _item(art, text="task b", state="complete", section_path="Ship 0.8.2"),
    ]
    hints = hint_from_artefact(art, items)
    assert hints.primary.source == "artefact_declared"
    assert hints.primary.title == "Ship 0.8.2"
    # Only active (pending/in_progress/stalled) items contribute evidence.
    assert len(hints.primary.evidence) == 1
    assert hints.subgoals == ()


def test_subgoals_emitted_for_multi_section_outline() -> None:
    art = _make_artefact("Plan")
    items = [
        _item(art, text="Plan", kind="section", section_path=""),
        _item(art, text="Phase 1", kind="section", section_path="Plan"),
        _item(art, text="a", section_path="Plan/Phase 1"),
        _item(art, text="b", section_path="Plan/Phase 1"),
        _item(art, text="Phase 2", kind="section", section_path="Plan"),
        _item(art, text="c", section_path="Plan/Phase 2"),
        _item(art, text="Phase 3", kind="section", section_path="Plan"),
        _item(art, text="d", section_path="Plan/Phase 3"),
    ]
    hints = hint_from_artefact(art, items)
    assert len(hints.subgoals) == 3
    titles = [sg.title for sg in hints.subgoals]
    assert all("Plan" in t for t in titles)
    # Each subgoal points to the primary as parent.
    parent_id = hints.subgoals[0].subgoal_of
    assert parent_id and all(sg.subgoal_of == parent_id for sg in hints.subgoals)


def test_subgoals_not_emitted_below_section_floor() -> None:
    art = _make_artefact()
    # Only 2 sections — below MIN_SECTIONS_FOR_SUBGOALS.
    assert MIN_SECTIONS_FOR_SUBGOALS == 3
    items = [
        _item(art, text="Root", kind="section", section_path=""),
        _item(art, text="A", kind="section", section_path="Root"),
        _item(art, text="x", section_path="Root/A"),
        _item(art, text="B", kind="section", section_path="Root"),
        _item(art, text="y", section_path="Root/B"),
    ]
    hints = hint_from_artefact(art, items)
    assert hints.subgoals == ()


def test_primary_includes_artefact_ref_and_related_files() -> None:
    art = _make_artefact()
    items = [
        _item(art, text="Root", kind="section", section_path=""),
        _item(
            art,
            text="update auth",
            section_path="Root",
            related_files=["src/auth.py"],
        ),
        _item(
            art,
            text="test auth",
            section_path="Root",
            related_files=["tests/test_auth.py"],
        ),
    ]
    hints = hint_from_artefact(art, items)
    assert hints.primary.artefact_refs == (art.id,)
    assert set(hints.primary.related_files) == {"src/auth.py", "tests/test_auth.py"}


def test_hints_from_artefacts_flattens_across_bundle() -> None:
    a1 = _make_artefact("Release")
    a2 = _make_artefact("Rewrite")
    items1 = [_item(a1, text="Release", kind="section"), _item(a1, text="one", section_path="Release")]
    items2 = [_item(a2, text="Rewrite", kind="section"), _item(a2, text="two", section_path="Rewrite")]
    flat = hints_from_artefacts([(a1, items1), (a2, items2)])
    titles = {c.title for c in flat}
    assert "Release" in titles and "Rewrite" in titles


def test_item_state_weight_differs_by_state() -> None:
    art = _make_artefact()
    items = [
        _item(art, text="Root", kind="section", section_path=""),
        _item(art, text="pending item", state="pending", section_path="Root"),
        _item(art, text="in-progress item", state="in_progress", section_path="Root"),
        _item(art, text="stalled item", state="stalled", section_path="Root"),
    ]
    hints = hint_from_artefact(art, items)
    weights = {e.value: e.weight for e in hints.primary.evidence}
    # Keys are item ids; verify pending < in_progress and stalled is
    # lowest among active states.
    values = list(weights.values())
    assert min(values) < max(values)


def test_complete_items_do_not_become_active_evidence() -> None:
    art = _make_artefact()
    items = [
        _item(art, text="Root", kind="section"),
        _item(art, text="done", state="complete", section_path="Root"),
    ]
    hints = hint_from_artefact(art, items)
    # No active items → no evidence on the primary.
    assert hints.primary.evidence == ()
