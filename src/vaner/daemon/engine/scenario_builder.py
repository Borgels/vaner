from __future__ import annotations

import hashlib
import time
from pathlib import Path

from vaner.intent.scenario_scorer import scenario_score
from vaner.models.artefact import Artefact
from vaner.models.scenario import EvidenceRef, Scenario, ScenarioCost, ScenarioKind


def _scenario_id(kind: str, entities: list[str]) -> str:
    digest = hashlib.sha256(f"{kind}:{'|'.join(sorted(entities))}".encode()).hexdigest()
    return f"scn_{digest[:16]}"


def _scenario_kind(path: str) -> ScenarioKind:
    lower = path.lower()
    if "test" in lower or "bug" in lower:
        return "debug"
    if lower.endswith((".md", ".mdx")):
        return "explain"
    if lower.endswith((".py", ".ts", ".tsx", ".js", ".rs", ".go")):
        return "change"
    return "research"


def _cost_for_path(path: str) -> ScenarioCost:
    if path.endswith((".md", ".mdx", ".txt")):
        return "low"
    if path.endswith((".py", ".ts", ".tsx", ".js")):
        return "medium"
    return "high"


def build_scenarios(
    repo_root: Path,
    artefacts: list[Artefact],
    changed_paths: list[str],
    *,
    max_scenarios: int = 200,
) -> list[Scenario]:
    now = time.time()
    by_path: dict[str, list[Artefact]] = {}
    for artefact in artefacts:
        by_path.setdefault(artefact.source_path, []).append(artefact)

    scenarios: list[Scenario] = []
    for path, items in by_path.items():
        kind = _scenario_kind(path)
        entities = [path]
        coverage_gaps: list[str] = []
        if path not in changed_paths:
            coverage_gaps.append("Not recently touched in git diff")
        if not any(item.kind.value == "diff_summary" for item in items):
            coverage_gaps.append("No fresh diff summary yet")
        evidence = [
            EvidenceRef(
                key=item.key,
                source_path=item.source_path,
                excerpt=item.content[:300],
                weight=max(0.0, float(item.relevance_score)),
            )
            for item in items[:6]
        ]
        prepared_context = "\n\n".join(f"[{item.kind.value}] {item.content[:1200]}" for item in items[:3])
        confidence = 0.5
        if path in changed_paths:
            confidence += 0.2
        confidence += min(0.2, len(items) * 0.04)
        scenario = Scenario(
            id=_scenario_id(kind, entities),
            kind=kind,
            confidence=round(min(confidence, 0.95), 3),
            entities=entities,
            evidence=evidence,
            prepared_context=prepared_context.strip(),
            coverage_gaps=coverage_gaps,
            freshness="fresh" if path in changed_paths else "recent",
            cost_to_expand=_cost_for_path(path),
            created_at=now,
            last_refreshed_at=now,
        )
        scenario.score = scenario_score(scenario)
        scenarios.append(scenario)
    scenarios.sort(key=lambda scenario: scenario.score, reverse=True)
    return scenarios[: max(1, max_scenarios)]
