from __future__ import annotations

import hashlib
import time
from pathlib import Path

from vaner.intent.scenario_scorer import scenario_score
from vaner.models.artefact import Artefact
from vaner.models.scenario import EvidenceRef, Scenario, ScenarioCost, ScenarioKind

_EXCLUDED_SUBSTRINGS = (".vaner_data/", ".mypy_cache/", "/requirements/", "requirements/")
_EXCLUDED_EXACT = {"requirements.txt", "poetry.lock", "uv.lock", "Pipfile.lock"}


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


def _is_excluded_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip("./")
    if any(part in normalized for part in _EXCLUDED_SUBSTRINGS):
        return True
    filename = normalized.split("/")[-1]
    return filename in _EXCLUDED_EXACT


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
        if _is_excluded_path(artefact.source_path):
            continue
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
        has_diff = any(item.kind.value == "diff_summary" for item in items)
        path_depth_penalty = min(0.12, max(0, len(path.split("/")) - 2) * 0.015)
        confidence = 0.35
        if path in changed_paths:
            confidence += 0.3
        if has_diff:
            confidence += 0.12
        confidence += min(0.24, len(items) * 0.05)
        confidence -= path_depth_penalty
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

    # Add grouped multi-entity scenarios for tightly related changed files.
    changed_by_parent: dict[str, list[str]] = {}
    for rel_path in changed_paths:
        if _is_excluded_path(rel_path):
            continue
        parent = str(Path(rel_path).parent)
        changed_by_parent.setdefault(parent, []).append(rel_path)
    for parent, related in changed_by_parent.items():
        unique_related = sorted(set(related))
        if len(unique_related) < 2:
            continue
        evidence: list[EvidenceRef] = []
        for rel_path in unique_related[:4]:
            for item in by_path.get(rel_path, [])[:2]:
                evidence.append(
                    EvidenceRef(
                        key=item.key,
                        source_path=item.source_path,
                        excerpt=item.content[:220],
                        weight=max(0.0, float(item.relevance_score)),
                    )
                )
        if not evidence:
            continue
        combined_context = "\n".join(f"- {ref.source_path}: {ref.excerpt[:140]}" for ref in evidence[:8])
        grouped = Scenario(
            id=_scenario_id("change", unique_related),
            kind="change",
            confidence=round(min(0.95, 0.55 + (len(unique_related) * 0.08)), 3),
            entities=unique_related,
            evidence=evidence[:8],
            prepared_context=f"Related changed files in {parent}:\n{combined_context}",
            coverage_gaps=[],
            freshness="fresh",
            cost_to_expand="medium",
            created_at=now,
            last_refreshed_at=now,
        )
        grouped.score = scenario_score(grouped)
        scenarios.append(grouped)
    scenarios.sort(key=lambda scenario: scenario.score, reverse=True)
    return scenarios[: max(1, max_scenarios)]
