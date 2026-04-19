# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from vaner.broker.selector import select_artefacts
from vaner.models.artefact import Artefact, ArtefactKind


def test_select_artefacts_prefers_prompt_matches():
    artefacts = [
        Artefact(
            key="file_summary:a.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="a.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="authentication and login flow",
        ),
        Artefact(
            key="file_summary:b.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="b.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="image rendering only",
        ),
    ]
    selected = select_artefacts("explain authentication", artefacts, top_n=1)
    assert selected[0].key == "file_summary:a.py"


def test_select_artefacts_prefers_git_and_working_set_matches():
    artefacts = [
        Artefact(
            key="file_summary:a.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="a.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="generic content",
        ),
        Artefact(
            key="file_summary:b.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="b.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="generic content",
        ),
    ]
    selected = select_artefacts(
        "explain flow",
        artefacts,
        top_n=1,
        preferred_paths={"b.py"},
        preferred_keys={"file_summary:b.py"},
    )
    assert selected[0].key == "file_summary:b.py"


def test_select_artefacts_origin_rerank_prefers_definition_files():
    artefacts = [
        Artefact(
            key="file_summary:a.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="a.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Functions: evaluate() Snippet: generic processing",
        ),
        Artefact(
            key="file_summary:b.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="b.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Functions: _is_stale_cache() Snippet: stale checks and refresh decisions",
        ),
    ]
    selected = select_artefacts("where is stale cache checked", artefacts, top_n=1)
    assert selected[0].key == "file_summary:b.py"


def test_select_artefacts_custom_scorer_changes_ranking():
    artefacts = [
        Artefact(
            key="file_summary:a.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="a.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="alpha",
        ),
        Artefact(
            key="file_summary:b.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="b.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="beta",
        ),
    ]

    def custom_scorer(_: str, artefact: Artefact) -> float:
        return 10.0 if artefact.key.endswith("b.py") else 0.0

    selected = select_artefacts("alpha", artefacts, top_n=1, scorer=custom_scorer)
    assert selected[0].key == "file_summary:b.py"


def test_select_artefacts_excludes_private_zone_when_requested():
    artefacts = [
        Artefact(
            key="file_summary:private.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="private.md",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="very relevant secret content",
            metadata={"privacy_zone": "private_local"},
        ),
        Artefact(
            key="file_summary:public.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="public.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="very relevant public content",
            metadata={"privacy_zone": "project_local"},
        ),
    ]
    selected = select_artefacts("relevant content", artefacts, top_n=2, exclude_private=True)
    assert selected
    assert all(a.metadata.get("privacy_zone") != "private_local" for a in selected)


def test_select_artefacts_applies_competitive_score_gate_on_fill_branch():
    artefacts = [
        Artefact(
            key="file_summary:top.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="top.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="top result",
            metadata={"corpus_id": "repo"},
        ),
        Artefact(
            key="file_summary:low_notes.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="low_notes.md",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="low result",
            metadata={"corpus_id": "notes"},
        ),
    ]

    def custom_scorer(_: str, artefact: Artefact) -> float:
        return 10.0 if artefact.key.endswith("top.py") else 2.0  # 2.0 < 10 * 0.45

    selected = select_artefacts("anything", artefacts, top_n=2, scorer=custom_scorer)
    assert [a.key for a in selected] == ["file_summary:top.py"]
