# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.target_normalization import component_terms, normalize_component, path_component_terms


def test_normalize_component_handles_case_styles_plural_and_artifact_spelling() -> None:
    assert normalize_component("ArtefactStore") == "artifactstore"
    assert normalize_component("artifact-store") == "artifactstore"
    assert normalize_component("artifact_stores") == "artifactstore"
    assert normalize_component("computeReward") == "computereward"
    assert normalize_component("compute_reward") == "computereward"


def test_component_terms_extract_dotted_members_and_path_tokens() -> None:
    terms = component_terms("Investigate ExplorationFrontier.plan_next in src/intent/frontier.py")

    assert "explorationfrontier" in terms
    assert "plannext" in terms
    assert "frontier" in terms


def test_path_component_terms_normalize_path_segments() -> None:
    assert "artifactstore" in path_component_terms("src/store/artefact-store.py")
