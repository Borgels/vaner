# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.store.artefacts import ArtefactStore


@pytest.mark.asyncio
async def test_pinned_facts_crud_and_scope_filtering(temp_repo):
    store = ArtefactStore(temp_repo / ".vaner" / "store.db")
    await store.initialize()

    await store.upsert_pinned_fact(
        key="prefer_source",
        value="arc",
        scope="user",
        scoring_hint={"kind": "prefer_source", "target": "arc"},
    )
    await store.upsert_pinned_fact(
        key="focus_paths",
        value="app/**",
        scope="project",
        scoring_hint={"kind": "focus_paths", "target": "app/**"},
    )

    all_rows = await store.list_pinned_facts()
    assert len(all_rows) == 2
    assert {row["scope"] for row in all_rows} == {"user", "project"}

    project_rows = await store.list_pinned_facts(scope="project")
    assert len(project_rows) == 1
    assert project_rows[0]["key"] == "focus_paths"

    await store.upsert_pinned_fact(
        key="focus_paths",
        value="src/**",
        scope="project",
        scoring_hint={"kind": "focus_paths", "target": "src/**"},
    )
    updated = await store.list_pinned_facts(scope="project")
    assert updated[0]["value"] == "src/**"

    removed = await store.remove_pinned_fact("prefer_source")
    assert removed is True
    remaining = await store.list_pinned_facts()
    assert len(remaining) == 1
    assert remaining[0]["key"] == "focus_paths"


@pytest.mark.asyncio
async def test_pinned_facts_overflow_raises_clear_error(temp_repo):
    store = ArtefactStore(temp_repo / ".vaner" / "store.db")
    await store.initialize()

    for index in range(50):
        await store.upsert_pinned_fact(key=f"pin_{index}", value=f"value_{index}", scope="user")

    with pytest.raises(ValueError, match="maximum 50 entries"):
        await store.upsert_pinned_fact(key="pin_overflow", value="value_overflow", scope="user")


@pytest.mark.asyncio
async def test_pinned_facts_allow_same_key_across_scopes(temp_repo):
    store = ArtefactStore(temp_repo / ".vaner" / "store.db")
    await store.initialize()

    await store.upsert_pinned_fact(key="focus_paths", value="app/**", scope="project")
    await store.upsert_pinned_fact(key="focus_paths", value="notes/**", scope="workflow")

    rows = await store.list_pinned_facts()
    focus_rows = [row for row in rows if row["key"] == "focus_paths"]
    assert len(focus_rows) == 2
    assert {row["scope"] for row in focus_rows} == {"project", "workflow"}
