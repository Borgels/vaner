# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from vaner.broker.context_preparation import infer_context_preparation_profile
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


def test_select_artefacts_prefers_exact_component_stem_over_broad_content_word():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/store/scenarios/queries.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/store/scenarios/queries.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Scenario storage queries and scenario listing helpers. Scenario scenario scenario.",
        ),
        Artefact(
            key="file_summary:src/vaner/intent/arcs.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/arcs.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="ConversationArcModel rank_next computes arc transitions and probabilities.",
        ),
    ]

    selected = select_artefacts("How do arc-based scenarios compute arc probability?", artefacts, top_n=1)

    assert selected[0].source_path == "src/vaner/intent/arcs.py"


def test_select_artefacts_prefers_cold_start_bootstrap_paths_over_warm_cache_mentions():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/intent/cache.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/cache.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Warm cache lookup, warm_start packages, warm package semantic matching. cached data cached data cached data.",
        ),
        Artefact(
            key="file_summary:src/vaner/daemon/runner.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/daemon/runner.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="run_once scans repository files and prepares corpus summaries on bootstrap.",
        ),
    ]

    selected = select_artefacts("Walk through cold start when a new repository has no cached data", artefacts, top_n=1)

    assert selected[0].source_path == "src/vaner/daemon/runner.py"


def test_select_artefacts_keeps_cold_start_bootstrap_above_proxy_flow():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/router/proxy.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/router/proxy.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Request query flow through proxy server and MCP routing.",
        ),
        Artefact(
            key="file_summary:src/vaner/engine.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/engine.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="prepare_corpus scan_repo_files cold_miss bootstrap behavior when no cached data exists.",
        ),
    ]

    selected = select_artefacts("Walk me through cold start when Vaner has no cached data", artefacts, top_n=1)

    assert selected[0].source_path == "src/vaner/engine.py"


def test_select_artefacts_prefers_hybrid_feature_training_sources():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/broker/selector.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/broker/selector.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Select runtime artefacts for context packages.",
        ),
        Artefact(
            key="file_summary:src/vaner/intent/features.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/features.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="extract_hybrid_features feature_vector_for_artefact artefact_age_seconds training examples.",
        ),
    ]

    selected = select_artefacts("How does the training pipeline extract hybrid features from artefact data?", artefacts, top_n=1)

    assert selected[0].source_path == "src/vaner/intent/features.py"


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


def test_select_artefacts_keeps_cache_tier_policy_files_competitive():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/events/predictions.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/events/predictions.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Prediction events emitted when cache entries change.",
        ),
        Artefact(
            key="file_summary:src/vaner/intent/scoring_policy.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/scoring_policy.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content=(
                "cache_full_hit_path_threshold cache_partial_hit_path_threshold "
                "cache_full_hit_similarity_threshold cache_partial_hit_similarity_threshold"
            ),
        ),
    ]

    selected = select_artefacts("Explain full hit partial hit warm start cache tier policy", artefacts, top_n=1)

    assert selected[0].source_path == "src/vaner/intent/scoring_policy.py"


def test_select_artefacts_does_not_treat_warm_start_as_cold_start_bootstrap():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/daemon/runner.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/daemon/runner.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="run_once scan_repo_files scan_repository summarize bootstrap empty repository cold cache preparation",
        ),
        Artefact(
            key="file_summary:src/vaner/intent/scoring_policy.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/scoring_policy.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content=(
                "cache_full_hit_path_threshold cache_partial_hit_path_threshold "
                "cache_full_hit_similarity_threshold cache_partial_hit_similarity_threshold"
            ),
        ),
        Artefact(
            key="file_summary:tests/test_intent/test_cache.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="tests/test_intent/test_cache.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="TieredPredictionCache full_hit partial_hit warm_start cold_miss expectations",
        ),
    ]

    selected = select_artefacts(
        "Explain the tiered prediction cache. How does it decide between full hit, partial hit, and warm start?",
        artefacts,
        top_n=2,
    )

    selected_paths = [item.source_path for item in selected]
    assert "src/vaner/daemon/runner.py" not in selected_paths
    assert selected_paths[0] == "src/vaner/intent/scoring_policy.py"
    assert set(selected_paths) == {"src/vaner/intent/scoring_policy.py", "tests/test_intent/test_cache.py"}


def test_select_artefacts_prioritizes_llm_exploration_flow_files():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/daemon/engine/scenario_builder.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/daemon/engine/scenario_builder.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="scenario builder creates candidate scenario objects for daemon runs",
        ),
        Artefact(
            key="file_summary:src/vaner/intent/scoring_policy.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/scoring_policy.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="priority scoring policy for scenario exploration",
        ),
        Artefact(
            key="file_summary:src/vaner/engine.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/engine.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="_explore_scenario_with_llm parses ranked_files follow_on semantic_intent and pushes adjacent scenarios",
        ),
        Artefact(
            key="file_summary:src/vaner/clients/llm_response.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/clients/llm_response.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="LLMResponse content wrapper for external LLM calls",
        ),
    ]

    selected = select_artefacts(
        "Explain the LLM exploration flow. How does Vaner use an external LLM to rank files and propose follow-on scenarios?",
        artefacts,
        top_n=2,
    )

    assert [item.source_path for item in selected] == [
        "src/vaner/engine.py",
        "src/vaner/clients/llm_response.py",
    ]


def test_select_artefacts_resolves_rollout_rehearsal_paraphrase():
    artefacts = [
        Artefact(
            key="file_summary:src/runtime/release_notes.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/runtime/release_notes.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Release notes formatter for changelog entries and deployment announcements.",
        ),
        Artefact(
            key="file_summary:src/runtime/traffic_escrow.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/runtime/traffic_escrow.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content=(
                "TrafficEscrow controller coordinates rehearse_proxy replay runs, "
                "smoke policy checks, and staged promote gates."
            ),
        ),
    ]

    selected = select_artefacts(
        "What prevents a candidate release from getting full traffic until replay and smoke checks pass?",
        artefacts,
        top_n=1,
    )

    assert selected[0].source_path == "src/runtime/traffic_escrow.py"


def test_select_artefacts_resolves_vectorization_region_paraphrase():
    artefacts = [
        Artefact(
            key="file_summary:src/incidents/general_latency.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/incidents/general_latency.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Generic latency incident notes for customer-facing status updates.",
        ),
        Artefact(
            key="file_summary:src/incidents/eu_apac_embedding_egress.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/incidents/eu_apac_embedding_egress.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content=(
                "Embedding batch incident: eu-west residency stamp lag caused "
                "ap-southeast edge fallback and cross-region egress."
            ),
        ),
    ]

    selected = select_artefacts(
        "Why did a Western Europe tenant get routed to a Southeast Asia edge during a vectorization spike?",
        artefacts,
        top_n=1,
    )

    assert selected[0].source_path == "src/incidents/eu_apac_embedding_egress.py"


def test_select_artefacts_resolves_low_precision_numeric_mode_paraphrase():
    artefacts = [
        Artefact(
            key="file_summary:src/runtime/model_limits.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/runtime/model_limits.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Model admission limits and request queue accounting.",
        ),
        Artefact(
            key="file_summary:src/runtime/precision_annealing.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/runtime/precision_annealing.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="kernel_stability_threshold precision annealing pass rate checks before stepping from fp32 to int8.",
        ),
    ]

    selected = select_artefacts(
        "What default pass rate is required before stepping down from the safest numeric mode in low bit inference?",
        artefacts,
        top_n=1,
    )

    assert selected[0].source_path == "src/runtime/precision_annealing.py"


def test_select_artefacts_prefers_reward_computation_source_over_generic_policy():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/intent/scoring_policy.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/scoring_policy.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="score weights priority scenario policy multiplicative nudges",
        ),
        Artefact(
            key="file_summary:src/vaner/learning/reward.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/learning/reward.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="compute_reward RewardInput cache_tier similarity quality_lift host_outcome judge_score reward_total reward_components",
        ),
    ]

    selected = select_artefacts(
        "How does the reward computation work? What signals does it combine to produce the final reward value?",
        artefacts,
        top_n=1,
    )

    assert selected[0].source_path == "src/vaner/learning/reward.py"


def test_select_artefacts_keeps_intent_scorer_and_features_together():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/intent/scorer.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/scorer.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="class IntentScorer HistGradientBoosting model score predict feature vector",
        ),
        Artefact(
            key="file_summary:src/vaner/intent/features.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/intent/features.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content=(
                "extract_hybrid_features feature_vector_for_artefact active signal flags replay priority "
                "reward target component weights"
            ),
        ),
        Artefact(
            key="file_summary:src/vaner/router/proxy.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/router/proxy.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="proxy routing request response",
        ),
    ]

    selected = select_artefacts(
        "How does the IntentScorer use GBDT models? What features does it extract and how are they combined?",
        artefacts,
        top_n=2,
    )

    assert {item.source_path for item in selected} == {"src/vaner/intent/scorer.py", "src/vaner/intent/features.py"}


def test_select_artefacts_prefers_artefact_store_schema_over_package_metadata():
    artefacts = [
        Artefact(
            key="file_summary:src/vaner/store/artefacts.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="src/vaner/store/artefacts.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="class ArtefactStore CREATE TABLE artefacts SELECT key INSERT INTO artefacts context packages persist retrieve schema",
        ),
        Artefact(
            key="file_summary:ui/cockpit/package.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="ui/cockpit/package.json",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="package scripts dependencies",
        ),
    ]

    selected = select_artefacts(
        "How does the ArtefactStore persist and retrieve context packages? What database schema does it use?",
        artefacts,
        top_n=1,
    )

    assert selected[0].source_path == "src/vaner/store/artefacts.py"


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


def test_context_profile_infers_multi_source_synthesis_without_benchmark_labels():
    profile = infer_context_preparation_profile(
        "List every customer escalation across Slack and Jira after March 2026 and summarize the common risk."
    )

    assert profile.need == "multi_source_synthesis"
    assert profile.archetype in {"general", "operator"}
    assert "slack" in profile.source_hints
    assert "jira" in profile.source_hints
    assert any(constraint.kind == "restrictive_language" and constraint.value == "after" for constraint in profile.constraints)


def test_select_artefacts_disables_global_gate_for_multi_source_context_need():
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
        return 10.0 if artefact.key.endswith("top.py") else 2.0

    selected = select_artefacts(
        "summarize all related evidence",
        artefacts,
        top_n=2,
        scorer=custom_scorer,
        context_need="multi_source_synthesis",
    )

    assert [a.key for a in selected] == ["file_summary:top.py", "file_summary:low_notes.md"]
