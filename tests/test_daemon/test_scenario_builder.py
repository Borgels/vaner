from __future__ import annotations

from pathlib import Path

from vaner.daemon.engine.scenario_builder import build_scenarios
from vaner.models.artefact import Artefact, ArtefactKind


def _artefact(path: str, kind: ArtefactKind, idx: int, score: float = 0.5) -> Artefact:
    return Artefact(
        key=f"{kind.value}:{path}:{idx}",
        kind=kind,
        source_path=path,
        source_mtime=0.0,
        generated_at=0.0,
        model="local",
        content=f"{kind.value} content for {path}",
        relevance_score=score,
    )


def test_build_scenarios_sets_kind_cost_and_coverage() -> None:
    artefacts = [
        _artefact("src/main.py", ArtefactKind.FILE_SUMMARY, 1, 0.9),
        _artefact("src/main.py", ArtefactKind.DIFF_SUMMARY, 2, 0.8),
        _artefact("docs/intro.mdx", ArtefactKind.FILE_SUMMARY, 3, 0.4),
    ]
    scenarios = build_scenarios(Path("."), artefacts, changed_paths=["src/main.py"])
    by_path = {scenario.entities[0]: scenario for scenario in scenarios}

    code = by_path["src/main.py"]
    docs = by_path["docs/intro.mdx"]
    assert code.kind == "change"
    assert code.cost_to_expand == "medium"
    assert code.freshness == "fresh"
    assert "No fresh diff summary yet" not in code.coverage_gaps
    assert docs.kind == "explain"
    assert docs.cost_to_expand == "low"
    assert "Not recently touched in git diff" in docs.coverage_gaps


def test_build_scenarios_applies_max_scenarios_cap() -> None:
    artefacts = [_artefact(f"src/file_{idx}.py", ArtefactKind.FILE_SUMMARY, idx, score=0.5) for idx in range(20)]
    scenarios = build_scenarios(Path("."), artefacts, changed_paths=[], max_scenarios=5)
    assert len(scenarios) == 5
