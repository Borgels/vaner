# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

import pytest

from vaner.broker.selector import select_artefacts
from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.models.artefact import Artefact, ArtefactKind


def _artefact(path: str) -> Artefact:
    now = time.time()
    return Artefact(
        key=f"file_summary:{path}",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path=path,
        source_mtime=now,
        generated_at=now,
        model="test",
        content="same content",
        metadata={"privacy_zone": "project_local", "corpus_id": "repo"},
    )


@pytest.mark.asyncio
async def test_prefer_source_pin_bumps_multiplier_by_point_one(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.initialize()
    baseline = float(engine._scoring_policy.source_multipliers.get("arc", 1.0))

    await engine.store.upsert_pinned_fact(
        key="prefer_source",
        value="arc",
        scope="user",
        scoring_hint={"kind": "prefer_source", "target": "arc"},
    )
    engine.invalidate_pinned_facts()
    await engine.initialize()

    updated = float(engine._scoring_policy.source_multipliers.get("arc", 1.0))
    assert updated == pytest.approx(min(2.0, baseline + 0.10), abs=1e-9)


@pytest.mark.asyncio
async def test_focus_paths_pin_promotes_matching_paths(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.initialize()
    await engine.store.upsert_pinned_fact(
        key="focus_paths",
        value="app/**",
        scope="project",
        scoring_hint={"kind": "focus_paths", "target": "app/**"},
    )
    engine.invalidate_pinned_facts()
    await engine.initialize()

    selected = select_artefacts(
        "show module",
        [_artefact("other/main.py"), _artefact("app/main.py")],
        top_n=1,
        scorer=lambda _prompt, _artefact: 1.0,
        path_bonuses=engine._pinned_focus_paths,
        path_excludes=engine._pinned_avoid_paths,
    )
    assert selected[0].source_path == "app/main.py"


@pytest.mark.asyncio
async def test_avoid_paths_pin_drops_matching_paths(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.initialize()
    await engine.store.upsert_pinned_fact(
        key="avoid_paths",
        value="vendor/**",
        scope="project",
        scoring_hint={"kind": "avoid_paths", "target": "vendor/**"},
    )
    engine.invalidate_pinned_facts()
    await engine.initialize()

    selected = select_artefacts(
        "show module",
        [_artefact("vendor/lib.py"), _artefact("src/main.py")],
        top_n=2,
        scorer=lambda _prompt, _artefact: 1.0,
        path_bonuses=engine._pinned_focus_paths,
        path_excludes=engine._pinned_avoid_paths,
    )
    assert [item.source_path for item in selected] == ["src/main.py"]
