# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from vaner.intent.arcs import derive_prompt_macro
from vaner.models.artefact import Artefact
from vaner.store.artefacts import ArtefactStore


async def extract_hybrid_features(
    store: ArtefactStore,
    *,
    prompt: str | None = None,
    source_key: str | None = None,
) -> dict[str, float]:
    now = time.time()
    signals = await store.list_signal_events(limit=400)
    queries = await store.list_query_history(limit=200)
    hypotheses = await store.list_hypotheses(limit=100)
    quality_issues = await store.list_quality_issues(limit=200)
    relationship_edges = await store.list_relationship_edges(limit=3000)
    prompt_macros = await store.list_prompt_macros(limit=100)
    habit_transitions = await store.list_habit_transitions(limit=100)
    workflow_phase = await store.get_workflow_phase_summary()

    recent_signals = [event for event in signals if now - event.timestamp <= 900]
    categories = [
        str(event.payload.get("category", "unknown")) for event in signals if event.source == "query" and event.kind == "query_issued"
    ]

    prompt_tokens = set(prompt.lower().split()) if prompt else set()
    hypothesis_overlap = 0
    for hypothesis in hypotheses:
        question = str(hypothesis.get("question", "")).lower()
        if any(token and token in question for token in prompt_tokens):
            hypothesis_overlap += 1

    propagation_degree = 0.0
    if source_key:
        outgoing = [edge for edge in relationship_edges if edge[0] == source_key]
        incoming = [edge for edge in relationship_edges if edge[1] == source_key]
        propagation_degree = float(len(outgoing) + len(incoming))

    prompt_macro = derive_prompt_macro(prompt or "") if prompt else "general"
    matching_macros = [row for row in prompt_macros if str(row.get("macro_key", "")) == prompt_macro]
    macro_support = max((int(row.get("use_count", 0)) for row in matching_macros), default=0)
    macro_confidence = max((float(row.get("confidence", 0.0)) for row in matching_macros), default=0.0)
    transition_support = sum(int(row.get("transition_count", 0)) for row in habit_transitions)
    skill_events = [event for event in signals if event.kind == "skill_loaded"]
    skill_presence = 1.0 if skill_events else 0.0
    prompt_kind = "change"
    prompt_lower = (prompt or "").lower()
    if any(token in prompt_lower for token in ("debug", "traceback", "exception", "failing", "fix")):
        prompt_kind = "debug"
    elif any(token in prompt_lower for token in ("explain", "why", "understand")):
        prompt_kind = "explain"
    elif any(token in prompt_lower for token in ("research", "investigate", "compare")):
        prompt_kind = "research"
    skill_kind_match = 0.0
    for event in skill_events:
        kind = str(event.payload.get("vaner_kind", "")).strip().lower()
        if kind and kind == prompt_kind:
            skill_kind_match += 1.0

    return {
        "signal_count_total": float(len(signals)),
        "signal_count_recent_15m": float(len(recent_signals)),
        "query_count_total": float(len(queries)),
        "hypothesis_count": float(len(hypotheses)),
        "quality_issue_count": float(len(quality_issues)),
        "quality_issue_high": float(sum(1 for issue in quality_issues if issue["severity"] == "high")),
        "relationship_edge_count": float(len(relationship_edges)),
        "relationship_degree": propagation_degree,
        "hypothesis_prompt_overlap": float(hypothesis_overlap),
        "query_category_debugging": float(categories.count("debugging")),
        "query_category_testing": float(categories.count("testing")),
        "query_category_implementation": float(categories.count("implementation")),
        "prompt_macro_support": float(macro_support),
        "prompt_macro_confidence": float(macro_confidence),
        "habit_transition_support": float(transition_support),
        "workflow_phase_known": 1.0 if workflow_phase else 0.0,
        "skill_presence": skill_presence,
        "skill_kind_match": skill_kind_match,
    }


def feature_vector_for_artefact(features: dict[str, float], artefact: Artefact) -> list[float]:
    # Order must exactly match FEATURE_KEYS in vaner.intent.trainer.
    return [
        features.get("signal_count_recent_15m", 0.0),
        features.get("query_count_total", 0.0),
        features.get("hypothesis_count", 0.0),
        features.get("quality_issue_count", 0.0),
        features.get("relationship_edge_count", 0.0),
        features.get("relationship_degree", 0.0),
        features.get("hypothesis_prompt_overlap", 0.0),
        features.get("prompt_macro_support", 0.0),
        features.get("prompt_macro_confidence", 0.0),
        features.get("habit_transition_support", 0.0),
        features.get("workflow_phase_known", 0.0),
        features.get("artefact_privacy_private", 0.0),
        features.get("artefact_corpus_repo", 0.0),
        features.get("pin_focus_match", 0.0),
        features.get("pin_avoid_match", 0.0),
        features.get("frontier_source_graph", 0.0),
        features.get("frontier_source_arc", 0.0),
        features.get("frontier_source_pattern", 0.0),
        features.get("frontier_source_llm_branch", 0.0),
        # Policy-derived signals (added in FEATURE_SCHEMA_VERSION v2)
        features.get("policy_signal_graph", 0.0),
        features.get("policy_signal_arc", 0.0),
        features.get("policy_signal_coverage_gap", 0.0),
        features.get("policy_signal_pattern", 0.0),
        features.get("policy_signal_freshness", 0.0),
        features.get("skill_presence", 0.0),
        features.get("skill_kind_match", 0.0),
        # Artefact-level fields (always last, matches trainer.py FEATURE_KEYS[-2:])
        float(artefact.access_count),
        float(max(0.0, time.time() - artefact.generated_at)),
    ]
