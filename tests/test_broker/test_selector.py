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


def test_select_artefacts_splits_camelcase_identifiers():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/intent/frontier.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/frontier.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Exploration frontier priority queue and admission control.",
        ),
        Artefact(
            key="file_summary:src/vaner/models/signal.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/models/signal.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Signal event payloads and lifecycle metadata.",
        ),
    ]

    selected = select_artefacts("How does ExplorationFrontier choose scenarios?", artefacts, top_n=1)

    assert selected[0].source_path == "src/vaner/intent/frontier.py"


def test_select_artefacts_prefers_source_over_incidental_tests():
    artefacts = [
        Artefact(
            key="file_summary:tests/test_cache.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="tests/test_cache.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="TieredPredictionCache full hit partial hit warm start cold miss assertions.",
        ),
        Artefact(
            key="file_summary:src/vaner/intent/cache.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/cache.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Classes: TieredPredictionCache Functions: match store_entry candidate_anchor_units.",
        ),
    ]

    selected = select_artefacts("Explain TieredPredictionCache full hit and cold miss logic", artefacts, top_n=1)

    assert selected[0].source_path == "src/vaner/intent/cache.py"


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


def test_select_artefacts_uses_lexical_floor_for_direct_doc_lookup():
    artefacts = [
        Artefact(
            key="file_summary:docs/zh/docs/deployment/fastapicloud.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/zh/docs/deployment/fastapicloud.md",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="FastAPI Cloud deployment dashboard login project hosting.",
        ),
        Artefact(
            key="file_summary:docs/en/docs/tutorial/dependencies/index.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/en/docs/tutorial/dependencies/index.md",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Tutorial - User Guide. Dependency Injection. FastAPI has a very powerful but intuitive Dependency Injection system.",
        ),
    ]

    def misleading_intent_scorer(_: str, artefact: Artefact) -> float:
        return 5.0 if "fastapicloud" in artefact.source_path else 0.0

    selected = select_artefacts(
        "Where does the official documentation introduce FastAPI dependency injection?",
        artefacts,
        top_n=1,
        scorer=misleading_intent_scorer,
    )

    assert selected[0].source_path == "docs/en/docs/tutorial/dependencies/index.md"


def test_select_artefacts_prefers_english_docs_for_english_direct_lookup():
    artefacts = [
        Artefact(
            key="file_summary:docs/zh/docs/tutorial/security/first-steps.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/zh/docs/tutorial/security/first-steps.md",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="FastAPI security first steps OAuth2 password bearer token authentication.",
        ),
        Artefact(
            key="file_summary:docs/en/docs/tutorial/security/first-steps.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/en/docs/tutorial/security/first-steps.md",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="FastAPI security first steps OAuth2 password bearer token authentication.",
        ),
    ]

    def translation_biased_scorer(_: str, artefact: Artefact) -> float:
        return 4.0 if "/zh/" in artefact.source_path else 0.0

    selected = select_artefacts(
        "Where do the official FastAPI docs introduce the security first steps?",
        artefacts,
        top_n=1,
        scorer=translation_biased_scorer,
    )

    assert selected[0].source_path == "docs/en/docs/tutorial/security/first-steps.md"


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
