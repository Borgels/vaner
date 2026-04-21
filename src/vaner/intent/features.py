# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
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
    active_corpus_id = str(queries[0].get("corpus_id", "default")) if queries else "default"
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

    follow_up_detected_by_scope: dict[tuple[str, str, str], int] = {}
    follow_up_accepted_by_scope: dict[tuple[str, str, str], int] = {}
    follow_up_last_seen_by_scope: dict[tuple[str, str, str], float] = {}
    for event in signals:
        if event.kind not in {"follow_up_offer_detected", "follow_up_offer_accepted"}:
            continue
        pattern_id = str(event.payload.get("phrase_pattern_id", "")).strip()
        if not pattern_id:
            continue
        macro_key = str(event.payload.get("prompt_macro", "")).strip() or "general"
        corpus_id = str(event.payload.get("corpus_id", "default")).strip() or "default"
        scope_key = (pattern_id, macro_key, corpus_id)
        follow_up_last_seen_by_scope[scope_key] = max(follow_up_last_seen_by_scope.get(scope_key, 0.0), float(event.timestamp))
        if event.kind == "follow_up_offer_detected":
            follow_up_detected_by_scope[scope_key] = follow_up_detected_by_scope.get(scope_key, 0) + 1
        else:
            follow_up_accepted_by_scope[scope_key] = follow_up_accepted_by_scope.get(scope_key, 0) + 1

    follow_up_offer_strength = 0.0
    decay_tau_seconds = 1800.0
    for scope_key, detected_count in follow_up_detected_by_scope.items():
        if detected_count <= 0:
            continue
        _pattern_id, macro_key, corpus_id = scope_key
        if macro_key != prompt_macro or corpus_id != active_corpus_id:
            continue
        accepted_count = follow_up_accepted_by_scope.get(scope_key, 0)
        acceptance_rate = max(0.0, min(1.0, accepted_count / max(1, detected_count)))
        last_seen = follow_up_last_seen_by_scope.get(scope_key, 0.0)
        age_seconds = max(0.0, now - last_seen)
        decay = math.exp(-age_seconds / decay_tau_seconds)
        follow_up_offer_strength += decay * acceptance_rate
    follow_up_offer_strength = max(0.0, min(1.0, follow_up_offer_strength))

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
        "follow_up_offer_strength": follow_up_offer_strength,
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
        features.get("follow_up_offer_strength", 0.0),
        # Artefact-level fields (always last, matches trainer.py FEATURE_KEYS[-2:])
        float(artefact.access_count),
        float(max(0.0, time.time() - artefact.generated_at)),
    ]
