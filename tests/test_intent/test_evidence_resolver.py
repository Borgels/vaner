# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass

from vaner.intent.evidence_hygiene import filter_evidence_paths, is_evidence_path_allowed
from vaner.intent.evidence_resolver import evidence_readiness, resolve_evidence_targets
from vaner.intent.prediction_v2 import structured_from_prediction_fields


@dataclass
class _Artefact:
    content: str


def test_evidence_hygiene_excludes_generated_cache_paths() -> None:
    paths = filter_evidence_paths(
        [
            "src/vaner/engine.py",
            ".vaner_data/repos/codesearchnet/images/architecture.png",
            ".mypy_cache/3.11/vaner/engine.meta.json",
            ".pytest_cache/README.md",
            "build/lib/module.py",
            "crates/vaner-contract/bindings/PredictedPrompt.ts",
            "docs/diagram.png",
            "tests/test_engine.py",
        ]
    )

    assert paths == ["src/vaner/engine.py", "tests/test_engine.py"]
    assert not is_evidence_path_allowed("/tmp/local/file.py")
    assert not is_evidence_path_allowed("../outside.py")
    assert not is_evidence_path_allowed(".vaner_data/repos/codesearchnet/images/architecture.png")
    assert not is_evidence_path_allowed("crates/vaner-contract/bindings/PredictedPrompt.ts")


def test_resolver_prefers_semantic_source_over_cache_filename_noise() -> None:
    structured = structured_from_prediction_fields(
        label="Explain exploration frontier priority scoring",
        description="How does Vaner decide which scenario to explore next?",
        anchor="exploration frontier priority",
        confidence=0.8,
    )
    targets = resolve_evidence_targets(
        structured,
        available_paths=[
            ".mypy_cache/3.11/vaner/intent/frontier.meta.json",
            "src/vaner/intent/frontier.py",
            "docs/overview.md",
        ],
        artefacts_by_key={
            "file_summary:src/vaner/intent/frontier.py": _Artefact(
                "ExplorationFrontier computes scenario priority from graph proximity, arc probability, coverage gap, patterns, freshness."
            ),
            "file_summary:docs/overview.md": _Artefact("General Vaner overview."),
        },
        top_k=3,
    )

    assert targets
    assert targets[0].path == "src/vaner/intent/frontier.py"
    assert targets[0].role == "primary"
    assert all(".mypy_cache" not in target.path for target in targets)


def test_evidence_readiness_requires_confident_direct_targets() -> None:
    structured = structured_from_prediction_fields(
        label="Explain cache matching",
        description="full hit partial hit warm start",
        anchor="cache matching",
        confidence=0.8,
    )
    targets = resolve_evidence_targets(
        structured,
        available_paths=["src/vaner/intent/cache.py", "src/vaner/engine.py"],
        artefacts_by_key={
            "file_summary:src/vaner/intent/cache.py": _Artefact(
                "TieredPredictionCache decides full_hit partial_hit warm_start from path overlap and semantic similarity."
            )
        },
    )

    ready, reason = evidence_readiness(targets, structured)

    assert ready, reason


def test_mechanism_query_does_not_become_ready_with_docs_only() -> None:
    structured = structured_from_prediction_fields(
        label="Explain daemon runner precompute cycles",
        description="What triggers a new cycle and how does the daemon manage background precompute?",
        anchor="daemon runner precompute cycles",
        confidence=0.8,
    )
    targets = resolve_evidence_targets(
        structured,
        available_paths=["ARCHITECTURE.md", "docs/engineering/daemon.md"],
        artefacts_by_key={
            "file_summary:ARCHITECTURE.md": _Artefact("Vaner architecture and daemon overview."),
            "file_summary:docs/engineering/daemon.md": _Artefact("Background precompute cycle overview."),
        },
    )

    ready, reason = evidence_readiness(targets, structured)

    assert targets
    assert all(target.role == "supporting" for target in targets)
    assert not ready
    assert reason in {"no_primary_evidence", "supporting_only_evidence"}


def test_mechanism_query_prefers_source_over_architecture_docs() -> None:
    structured = structured_from_prediction_fields(
        label="Explain reward computation",
        description="What signals does reward computation combine to produce the final reward value?",
        anchor="reward computation",
        confidence=0.8,
    )
    targets = resolve_evidence_targets(
        structured,
        available_paths=[
            "ARCHITECTURE.md",
            "src/vaner/learning/reward.py",
            "docs/engineering/reward.md",
        ],
        artefacts_by_key={
            "file_summary:ARCHITECTURE.md": _Artefact("High-level architecture overview mentions reward computation."),
            "file_summary:src/vaner/learning/reward.py": _Artefact(
                "compute_reward combines cache tier, similarity, quality lift, judge score, and latency signals."
            ),
            "file_summary:docs/engineering/reward.md": _Artefact("Reward design notes."),
        },
    )

    assert targets[0].path == "src/vaner/learning/reward.py"
    assert targets[0].role == "primary"


def test_docs_can_be_primary_for_architecture_shaped_prediction() -> None:
    structured = structured_from_prediction_fields(
        label="Explain architecture overview",
        description="Summarize the documented architecture of Vaner",
        anchor="ARCHITECTURE.md",
        confidence=0.8,
    )
    targets = resolve_evidence_targets(
        structured,
        available_paths=["ARCHITECTURE.md", "docs/engineering/overview.md"],
        artefacts_by_key={
            "file_summary:ARCHITECTURE.md": _Artefact("Architecture overview for Vaner components and data flow."),
        },
    )

    ready, reason = evidence_readiness(targets, structured)

    assert targets[0].role == "primary"
    assert ready, reason


def test_definition_relation_outranks_stale_working_set_cluster() -> None:
    structured = structured_from_prediction_fields(
        label="Implement ArtefactStore",
        description="Add the concrete storage component",
        anchor="ArtefactStore",
        confidence=0.8,
    )

    targets = resolve_evidence_targets(
        structured,
        available_paths=["src/vaner/store/artefacts.py", "src/vaner/intent/cache.py"],
        artefacts_by_key={
            "file_summary:src/vaner/store/artefacts.py": _Artefact("class ArtefactStore persists artefacts and metadata."),
            "file_summary:src/vaner/intent/cache.py": _Artefact("TieredPredictionCache handles stale cluster cache hits."),
        },
        working_set={"src/vaner/intent/cache.py": 1.0},
    )

    assert targets[0].path == "src/vaner/store/artefacts.py"
    assert targets[0].symbol_relation == "definition_match"
    assert "stale_cluster_won" in targets[1].wrong_primary_reasons


def test_weak_component_match_stays_evidence_ready_only() -> None:
    structured = structured_from_prediction_fields(
        label="Implement ArtefactStore",
        description="Add the concrete storage component",
        anchor="ArtefactStore",
        confidence=0.8,
    )
    targets = resolve_evidence_targets(
        structured,
        available_paths=["docs/storage.md"],
        artefacts_by_key={"file_summary:docs/storage.md": _Artefact("Storage overview mentions cache clusters.")},
    )

    ready, reason = evidence_readiness(targets, structured)

    assert not ready
    assert reason == "weak_component_match"
