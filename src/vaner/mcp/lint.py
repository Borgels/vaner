# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass

from vaner.store.scenarios.sqlite import ScenarioStore


@dataclass(slots=True)
class LintReport:
    stale_areas: list[str]
    orphan_entities: list[str]
    contradictions: list[str]
    coverage_gaps: list[str]
    hot_areas: list[str]
    trusted_count: int
    candidate_count: int
    stale_count: int
    demoted_count: int


async def run_lint(store: ScenarioStore, *, top_n: int = 50) -> LintReport:
    scenarios = await store.list_top(limit=max(1, min(top_n, 50)))
    entity_map: dict[str, list[tuple[str, float, str, str | None]]] = {}
    coverage: set[str] = set()
    hot_counter: dict[str, int] = {}
    contradictions: list[str] = []

    for idx, scenario in enumerate(scenarios):
        coverage.update(scenario.coverage_gaps)
        for entity in scenario.entities:
            entity_map.setdefault(entity, []).append((scenario.id, scenario.score, scenario.memory_state, scenario.last_outcome))
            if idx < 10 and scenario.freshness != "stale":
                hot_counter[entity] = hot_counter.get(entity, 0) + 1

    stale_areas = sorted(entity for entity, refs in entity_map.items() if refs and all(state == "stale" for _, _, state, _ in refs))
    orphan_entities = sorted(entity for entity, refs in entity_map.items() if len(refs) == 1 and refs[0][1] < 0.2)

    for left in scenarios:
        for right in scenarios:
            if left.id >= right.id:
                continue
            shared = set(left.entities) & set(right.entities)
            if len(shared) < 3:
                continue
            left_wrong = left.last_outcome == "wrong"
            right_wrong = right.last_outcome == "wrong"
            left_useful = left.last_outcome == "useful"
            right_useful = right.last_outcome == "useful"
            state_conflict = {left.memory_state, right.memory_state} == {"trusted", "demoted"}
            if (left_wrong and right_useful) or (right_wrong and left_useful) or state_conflict:
                contradictions.append(f"{left.id}+{right.id}:{','.join(sorted(shared)[:4])}")

    counts = await store.memory_state_counts()
    hot_areas = [key for key, _ in sorted(hot_counter.items(), key=lambda item: item[1], reverse=True)]
    return LintReport(
        stale_areas=stale_areas,
        orphan_entities=orphan_entities,
        contradictions=sorted(set(contradictions)),
        coverage_gaps=sorted(coverage),
        hot_areas=hot_areas,
        trusted_count=int(counts.get("trusted", 0)),
        candidate_count=int(counts.get("candidate", 0)),
        stale_count=int(counts.get("stale", 0)),
        demoted_count=int(counts.get("demoted", 0)),
    )
