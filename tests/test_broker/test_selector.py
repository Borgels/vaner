# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

import pytest

from vaner.broker.aggregation import build_source_aggregation
from vaner.broker.context_preparation import (
    infer_context_preparation_profile,
    query_variants,
    semantic_path_hint_candidates,
    source_path_hint_candidates,
)
from vaner.broker.preparation_policy import choose_preparation_plan, plan_tool_names
from vaner.broker.scheduling import build_scheduling_evidence
from vaner.broker.selector import select_artefacts, select_artefacts_fts
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.store.artefacts import ArtefactStore


async def _fake_embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        lowered = text.lower()
        rollout = 1.0 if any(term in lowered for term in ("candidate", "release", "traffic", "replay", "smoke", "escrow")) else 0.0
        auth = 1.0 if any(term in lowered for term in ("auth", "credential", "token")) else 0.0
        vectors.append([rollout, auth])
    return vectors


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


def test_select_artefacts_uses_bounded_source_rank_prior():
    now = time.time()
    artefacts = [
        Artefact(
            key="file_summary:late.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="late.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="tenant cutover latency threshold escalation sustained",
            metadata={"retrieval_rank": 12},
        ),
        Artefact(
            key="file_summary:early.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="early.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="tenant cutover latency threshold escalation sustained",
            metadata={"retrieval_rank": 1},
        ),
    ]
    factors: dict[str, list] = {}

    selected = select_artefacts(
        "tenant cutover latency threshold escalation sustained",
        artefacts,
        top_n=1,
        capture_factors=factors,
    )

    assert selected[0].key == "file_summary:early.md"
    assert any(factor.name == "source_rank_prior" for factor in factors["file_summary:early.md"])


def test_query_variants_add_local_keyword_and_alias_recall_terms():
    prompt = "What prevents a candidate release from getting full traffic until replay and smoke checks pass?"
    profile = infer_context_preparation_profile(prompt)

    variants = " ".join(query_variants(prompt, profile))

    assert "candidate release" in variants
    assert "escrow" in variants
    assert "promote" in variants


def test_query_variants_add_scheduling_and_private_hosting_aliases():
    prompt = (
        "When is the 60 to 90 minute technical deep dive scheduled with the healthcare client "
        "about running model serving inside their own isolated network?"
    )
    profile = infer_context_preparation_profile(prompt)

    variants = " ".join(query_variants(prompt, profile, max_variants=8))

    assert "calendar invite booking" in variants
    assert "architecture review private hosting isolated network" in variants


def test_source_path_hints_surface_source_class_candidates():
    paths = [
        "slack/incidents/random-thread.json",
        "confluence/oncall-and-incident-response/postmortems/streaming-stalls.json",
        "confluence/oncall-and-incident-response/postmortems/rate-limit-misconfig.json",
    ]

    selected = source_path_hint_candidates("Across all incident postmortems, which team owned action items?", paths)

    assert selected[:2] == [
        "confluence/oncall-and-incident-response/postmortems/rate-limit-misconfig.json",
        "confluence/oncall-and-incident-response/postmortems/streaming-stalls.json",
    ]


def test_semantic_path_hints_bridge_indirect_prompt_to_canonical_source_names():
    paths = [
        "github/pr-39751-kernel-stability-thresholds-precision-annealing-kv-sync-guard.json",
        "github/pr-874321-traffic-escrow-and-rehearse-proxy-for-staged-promotes.json",
        "docs/team-handbook/weekly-planning.md",
    ]
    prompt = "What made low bit math safer before a machine steps down from the safest numeric mode?"
    profile = infer_context_preparation_profile(prompt)

    selected = semantic_path_hint_candidates(prompt, paths, profile=profile, limit=2)

    assert selected[0] == "github/pr-39751-kernel-stability-thresholds-precision-annealing-kv-sync-guard.json"


def test_source_and_semantic_path_hints_support_private_healthcare_deployments():
    paths = [
        "google_drive/users/valehealth-implementation-capture.json",
        "google_drive/users/acme-public-roadmap-notes.json",
        "slack/random/lunch-thread.json",
    ]
    prompt = "Find the hospital implementation notes for running an intake chatbot inside a locked-down data center."
    profile = infer_context_preparation_profile(prompt)

    source_hints = source_path_hint_candidates(prompt, paths)
    semantic_hints = semantic_path_hint_candidates(prompt, paths, profile=profile)

    assert "google_drive/users/valehealth-implementation-capture.json" in source_hints
    assert "google_drive/users/valehealth-implementation-capture.json" in semantic_hints


@pytest.mark.asyncio
async def test_select_artefacts_fts_loads_artefact_structure_candidates(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    now = time.time()
    target = Artefact(
        key="file_summary:precision-annealing.json",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="github/pr-39751-kernel-stability-thresholds-precision-annealing-kv-sync-guard.json",
        source_mtime=now,
        generated_at=now,
        model="test",
        content="Default stability pass threshold is 0.995 before leaving safe fp32 mode.",
    )
    distractor = Artefact(
        key="file_summary:planning.md",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="docs/team-handbook/weekly-planning.md",
        source_mtime=now,
        generated_at=now,
        model="test",
        content="Weekly planning notes and team rituals.",
    )
    await store.upsert(target)
    await store.upsert(distractor)

    selected = await select_artefacts_fts(
        "What made low bit math safer before a machine steps down from the safest numeric mode?",
        store,
        top_n=1,
    )

    assert selected[0].key == "file_summary:precision-annealing.json"
    assert "artefact_structure" in selected[0].metadata["context_sources"]


def test_source_path_hints_rank_specific_scheduling_threads():
    paths = [
        "gmail/team/generic-deepdive-scheduler.json",
        "gmail/soojin/architecture-review-booking-next-steps-cytohealth.json",
        "hubspot/company-health-network.json",
    ]
    prompt = (
        "When is the technical deep dive scheduled with the healthcare client "
        "about running model serving inside their own isolated network?"
    )

    selected = source_path_hint_candidates(prompt, paths)

    assert selected[0] == "gmail/soojin/architecture-review-booking-next-steps-cytohealth.json"


def test_source_evidence_profile_uses_evidence_mode():
    prompt = "What source evidence supports the rollout claim?"
    profile = infer_context_preparation_profile(prompt)

    assert profile.need == "source_evidence"
    assert any("source evidence claim citation provenance" in variant for variant in query_variants(prompt, profile))


def test_preparation_policy_names_bounded_context_tools():
    profile = infer_context_preparation_profile(
        "Across all incident reviews, which owner had the highest number of follow-up tasks?"
    )

    plan = choose_preparation_plan(profile, "Across all incident reviews, which owner had the highest number of follow-up tasks?")

    assert plan.name == "multi_source_synthesis"
    assert plan.deterministic is True
    assert [step.tool for step in plan.steps] == [
        "source_class_search",
        "semantic_search",
        "aggregate_sources",
        "coverage_check",
    ]


def test_preparation_policy_names_scheduling_evidence_tool():
    prompt = "When is the technical deep dive scheduled with the healthcare client?"
    profile = infer_context_preparation_profile(prompt)
    plan = choose_preparation_plan(profile, prompt)

    assert profile.need == "scheduling"
    assert [step.tool for step in plan.steps] == [
        "time_entity_extraction",
        "source_class_search",
        "semantic_search",
        "conflict_scan",
        "prepare_scheduling_evidence",
        "coverage_check",
    ]


def test_source_aggregation_preserves_group_counts_and_provenance():
    now = time.time()
    prompt = "Across all incident reviews, which team owned the most follow-up action items?"
    profile = infer_context_preparation_profile(prompt)
    artefacts = [
        Artefact(
            key="file_summary:runtime.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/incidents/postmortems/runtime-latency.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Post-incident review. Follow-up action items assigned to Runtime and SRE.",
        ),
        Artefact(
            key="file_summary:runtime-2.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/incidents/postmortems/rate-limit.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Review notes. Owner: Runtime. Action items track throttling defaults.",
        ),
        Artefact(
            key="file_summary:data.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/incidents/postmortems/replay-gap.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Incident review. Assigned to Data Platform for replay coverage.",
        ),
    ]

    aggregate, trace = build_source_aggregation(prompt, artefacts, profile)

    assert aggregate is not None
    assert trace.tool == "aggregate_sources"
    assert trace.output_count == 1
    assert "Runtime: 2 item(s)" in aggregate.content
    assert "Representative provenance:" in aggregate.content
    assert aggregate.metadata["aggregation_source_count"] == 3
    runtime_group = next(group for group in aggregate.metadata["aggregation_groups"] if group["value"] == "Runtime")
    assert runtime_group["count_basis"] == "extracted_item_count"
    assert runtime_group["source_keys"] == ["file_summary:runtime.json", "file_summary:runtime-2.json"]
    assert {item["source_key"] for item in runtime_group["evidence"]} == {
        "file_summary:runtime.json",
        "file_summary:runtime-2.json",
    }
    assert aggregate.metadata["count_basis"] == "extracted_item_count"
    assert aggregate.metadata["aggregation_extraction_spec"]["item_type"] == "action_item"
    assert aggregate.metadata["extracted_row_count"] == 4
    assert runtime_group["extracted_rows"][0]["group"]["normalized_value"] == "Runtime"
    assert aggregate.metadata["provenance_coverage_count"] == 3
    assert aggregate.metadata["provenance_truncated"] is False


def test_source_aggregation_normalizes_owning_team_prefixes():
    now = time.time()
    prompt = "Across all incident reviews, which team owned the most follow-up action items?"
    profile = infer_context_preparation_profile(prompt)
    artefacts = [
        Artefact(
            key="file_summary:control-plane.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/incidents/postmortems/control-plane.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content=(
                "## Follow-up action items\n"
                "1) Add schema validation. Owning team: Eng Platform.\n"
                "2) Add rollback smoke test. Owning team: Eng Platform.\n"
            ),
        ),
        Artefact(
            key="file_summary:streaming.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/incidents/postmortems/streaming.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content=(
                "## Follow-up action items\n"
                "1) Add stream stall alert. Owning team: Eng SRE.\n"
                "2) Document gateway ownership. Owner team: Platform.\n"
            ),
        ),
        Artefact(
            key="file_summary:rbac.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/incidents/postmortems/rbac.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content=(
                "## Follow-up action items\n"
                "1) Add policy diff approval. Owner team: Security.\n"
            ),
        ),
    ]

    aggregate, _trace = build_source_aggregation(prompt, artefacts, profile)

    assert aggregate is not None
    groups = {group["value"]: group for group in aggregate.metadata["aggregation_groups"]}
    assert groups["Platform"]["count"] == 3
    assert groups["SRE"]["count"] == 1
    assert "Eng Platform" not in groups
    assert "Eng SRE" not in groups


def test_source_aggregation_counts_decision_rows_by_owner():
    now = time.time()
    prompt = "Across the design reviews, count decisions by DRI."
    profile = infer_context_preparation_profile(prompt)
    artefacts = [
        Artefact(
            key="file_summary:search-review.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/design-reviews/search-review.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content=(
                "## Decisions\n"
                "- Adopt hybrid search for recall-sensitive queries. DRI: Search Platform.\n"
                "- Keep lexical fallback for exact references. DRI: Search Platform.\n"
            ),
        ),
        Artefact(
            key="file_summary:billing-review.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/design-reviews/billing-review.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content=(
                "## Decisions\n"
                "- Defer metering schema migration until export tests pass. DRI: Billing Infra.\n"
            ),
        ),
        Artefact(
            key="file_summary:notes.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/design-reviews/notes.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Meeting notes with no decision owner.",
        ),
    ]

    aggregate, trace = build_source_aggregation(prompt, artefacts, profile)

    assert aggregate is not None
    assert "count_basis:extracted_item_count" in trace.notes
    assert aggregate.metadata["aggregation_extraction_spec"]["item_type"] == "decision"
    assert aggregate.metadata["aggregation_extraction_spec"]["group_by"] == "owner"
    assert aggregate.metadata["extracted_row_count"] == 3
    groups = {group["value"]: group for group in aggregate.metadata["aggregation_groups"]}
    assert groups["Search Platform"]["count"] == 2
    assert groups["Billing Infra"]["count"] == 1
    assert groups["Search Platform"]["extracted_rows"][0]["item_type"] == "decision"


def test_source_aggregation_scopes_extraction_to_requested_project():
    now = time.time()
    prompt = "Across project Atlas design reviews, count decisions by DRI."
    profile = infer_context_preparation_profile(prompt)
    artefacts = [
        Artefact(
            key="file_summary:atlas-search.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/design-reviews/atlas-search.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content=(
                "Project Atlas design review\n"
                "## Decisions\n"
                "- Adopt hybrid search for Atlas recall. DRI: Search Platform.\n"
                "- Keep lexical fallback for Atlas exact references. DRI: Search Platform.\n"
            ),
        ),
        Artefact(
            key="file_summary:atlas-billing.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/design-reviews/atlas-billing.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content=(
                "Project Atlas design review\n"
                "## Decisions\n"
                "- Defer metering migration until export tests pass. DRI: Billing Infra.\n"
            ),
        ),
        Artefact(
            key="file_summary:zephyr-search.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/design-reviews/zephyr-search.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content=(
                "Project Zephyr design review\n"
                "## Decisions\n"
                "- Replace indexing backend for Zephyr. DRI: Search Platform.\n"
                "- Move Zephyr rollout ownership. DRI: Release Eng.\n"
            ),
        ),
    ]

    aggregate, trace = build_source_aggregation(prompt, artefacts, profile)

    assert aggregate is not None
    assert "eligible_sources:2" in trace.notes
    assert "excluded_sources:1" in trace.notes
    assert aggregate.metadata["candidate_scope_spec"]["required_facets"] == ["Atlas", "design review"]
    assert aggregate.metadata["candidate_pool_count"] == 3
    assert aggregate.metadata["eligible_source_count"] == 2
    assert aggregate.metadata["excluded_source_count"] == 1
    assert aggregate.metadata["extraction_source_count"] == 2
    assert aggregate.metadata["extraction_source_keys"] == [
        "file_summary:atlas-search.md",
        "file_summary:atlas-billing.md",
    ]
    assert aggregate.metadata["extracted_row_count"] == 3
    assert aggregate.metadata["counted_row_count"] == 3
    assert aggregate.metadata["dropped_row_count"] == 0
    assert aggregate.metadata["counted_source_count"] == 2
    assert aggregate.metadata["counted_source_keys"] == [
        "file_summary:atlas-search.md",
        "file_summary:atlas-billing.md",
    ]
    excluded = aggregate.metadata["excluded_sources"][0]
    assert excluded["source_key"] == "file_summary:zephyr-search.md"
    assert "missing_required_facet:Atlas" in excluded["reasons"]
    groups = {group["value"]: group for group in aggregate.metadata["aggregation_groups"]}
    assert groups["Search Platform"]["count"] == 2
    assert groups["Billing Infra"]["count"] == 1
    assert groups["Search Platform"]["extracted_rows"][0]["scope_status"] == "counted"
    assert "Release Eng" not in groups


def test_scheduling_evidence_prepares_confirmed_time_source():
    now = time.time()
    prompt = (
        "When is the 60 to 90 minute technical deep dive scheduled with the healthcare client "
        "about running model serving inside their own isolated network, and what is the time window in Pacific time?"
    )
    profile = infer_context_preparation_profile(prompt)
    artefacts = [
        Artefact(
            key="file_summary:generic.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="gmail/sales/generic-private-review.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Proposed private hosting review slots next month. No confirmed attendees yet.",
        ),
        Artefact(
            key="file_summary:confirmed.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="gmail/solutions/architecture-review-booking-next-steps-cytohealth.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content=(
                "CytoHealth private VPC architecture review. Tue Oct 24 10:00-11:15 PT works for our team. "
                "Invite attached invite-20281024.ics. PHI-sensitive workloads and isolated network deployment."
            ),
        ),
    ]

    scheduling, trace = build_scheduling_evidence(prompt, artefacts, profile)

    assert scheduling is not None
    assert trace.output_count == 1
    assert scheduling.metadata["scheduling_evidence_source_keys"][0] == "file_summary:confirmed.json"
    assert scheduling.metadata["scheduling_evidence"][0]["status"] == "confirmed"
    assert "10:00-11:15 PT" in scheduling.content


@pytest.mark.asyncio
async def test_select_artefacts_fts_uses_semantic_memory_source(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    now = time.time()
    rollout = Artefact(
        key="file_summary:rollout.md",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="docs/rollout.md",
        source_mtime=now,
        generated_at=now,
        model="test",
        content="Traffic escrow rehearses replay and smoke policy checks before promote.",
    )
    unrelated = Artefact(
        key="file_summary:auth.md",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="docs/auth.md",
        source_mtime=now,
        generated_at=now,
        model="test",
        content="Credential rotation and token validation notes.",
    )
    await store.upsert(rollout)
    await store.upsert(unrelated)
    await store.index_artefact_semantic(rollout, embed=_fake_embed, embedding_model="fake")
    await store.index_artefact_semantic(unrelated, embed=_fake_embed, embedding_model="fake")

    selected = await select_artefacts_fts(
        "candidate release gets full traffic after checks",
        store,
        top_n=1,
        semantic_memory_enabled=True,
        semantic_embed=_fake_embed,
    )

    assert selected[0].key == "file_summary:rollout.md"
    assert "semantic_memory" in selected[0].metadata["context_sources"]


@pytest.mark.asyncio
async def test_select_artefacts_fts_injects_prepared_aggregation_for_multi_source_synthesis(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    now = time.time()
    for artefact in [
        Artefact(
            key="file_summary:runtime.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/oncall/postmortems/runtime-latency.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Postmortem. Follow-up action items assigned to Runtime and SRE after incident review.",
        ),
        Artefact(
            key="file_summary:runtime-2.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/oncall/postmortems/rate-limit.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Postmortem. Owner: Runtime. Action items cover throttling and monitoring.",
        ),
        Artefact(
            key="file_summary:data.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/oncall/postmortems/replay-gap.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Postmortem. Assigned to Data Platform for replay and audit coverage.",
        ),
        Artefact(
            key="file_summary:template.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/oncall/postmortems/template.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Postmortem template. Assigned team placeholder.",
        ),
    ]:
        await store.upsert(artefact)
    diagnostics = []

    selected = await select_artefacts_fts(
        "Across all incident postmortems, which team was assigned the most follow-up action items?",
        store,
        top_n=4,
        capture_prepared_context_diagnostics=diagnostics,
    )

    assert selected[0].key.startswith("prepared_context:source_aggregation:")
    assert "Runtime: 2 item(s)" in selected[0].content
    assert diagnostics[-1].preparation_plan is not None
    assert diagnostics[-1].preparation_plan.name == "multi_source_synthesis"
    assert diagnostics[-1].aggregation_count == 1
    assert any(trace.tool == "aggregate_sources" and trace.output_count == 1 for trace in diagnostics[-1].tool_traces)


def test_date_constraints_report_but_do_not_drop_relevant_evidence_per_document():
    now = time.time()
    prompt = "In March 2026, which deployment offering had the most request timeouts?"
    profile = infer_context_preparation_profile(prompt)
    artefacts = [
        Artefact(
            key="file_summary:timeout.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="support/timeout.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Hosted API request timeout escalation with customer impact.",
        )
    ]

    selected = select_artefacts(
        prompt,
        artefacts,
        top_n=1,
        context_need=profile.need,
        context_profile=profile,
        enabled_context_tools=plan_tool_names(choose_preparation_plan(profile, prompt)),
    )

    assert selected[0].key == "file_summary:timeout.md"


def test_select_artefacts_backfills_deferred_diversity_candidates():
    now = time.time()
    artefacts = [
        Artefact(
            key=f"file_summary:strong-{index}.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path=f"docs/shared/strong-{index}.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="rollback rollback rollback conflict current updated baseline score",
        )
        for index in range(4)
    ]
    artefacts.extend(
        [
            Artefact(
                key="file_summary:coverage-a.md",
                kind=ArtefactKind.FILE_SUMMARY,
                source_path="docs/coverage-a/source.md",
                source_mtime=now,
                generated_at=now,
                model="test",
                content="baseline score",
            ),
            Artefact(
                key="file_summary:coverage-b.md",
                kind=ArtefactKind.FILE_SUMMARY,
                source_path="docs/coverage-b/source.md",
                source_mtime=now,
                generated_at=now,
                model="test",
                content="updated score",
            ),
        ]
    )

    selected = select_artefacts(
        "compare current updated rollback conflict baseline score",
        artefacts,
        top_n=4,
        context_need="conflict_resolution",
    )

    selected_keys = {artefact.key for artefact in selected}
    assert "file_summary:strong-1.md" in selected_keys
    assert "file_summary:strong-2.md" in selected_keys


def test_select_artefacts_seeds_coverage_for_multi_source_synthesis():
    now = time.time()
    artefacts = [
        Artefact(
            key="file_summary:hosted.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/hosted.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Hosted API support escalations auth incident customer escalation escalation escalation escalation",
            metadata={"retrieval_rank": 1},
        ),
        Artefact(
            key="file_summary:dedicated.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/dedicated.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Dedicated deployment support escalations auth customers",
            metadata={"retrieval_rank": 40},
        ),
        Artefact(
            key="file_summary:private.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/private.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Private deployment support escalations auth customers",
            metadata={"retrieval_rank": 41},
        ),
        Artefact(
            key="file_summary:generic.md",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="docs/generic.md",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="support escalations auth customers escalation escalation escalation escalation escalation",
            metadata={"retrieval_rank": 2},
        ),
    ]
    prompt = "Across Hosted API, Dedicated, and Private deployment offerings, which had the most auth-related support escalations?"
    profile = infer_context_preparation_profile(prompt)

    selected = select_artefacts(prompt, artefacts, top_n=3, context_need=profile.need, context_profile=profile)

    selected_paths = {artefact.source_path for artefact in selected}
    assert {"docs/hosted.md", "docs/dedicated.md", "docs/private.md"} <= selected_paths


def test_multi_source_aggregation_prefers_canonical_postmortem_docs():
    now = time.time()
    artefacts = [
        Artefact(
            key="file_summary:slack-postmortem.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="slack/postmortems/action-items-thread.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="postmortem action items action items assigned team action items",
        ),
        Artefact(
            key="file_summary:template.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/oncall/postmortems/postmortem-template.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Postmortem template for assigned team action items",
        ),
        Artefact(
            key="file_summary:canonical.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="confluence/oncall/postmortems/streaming-stalls-2026-01-12.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Postmortem: Streaming stalls. Follow-up action items assigned to runtime and SRE.",
        ),
    ]
    prompt = "Across all incident postmortems, which team was assigned the most follow-up action items?"
    profile = infer_context_preparation_profile(prompt)

    selected = select_artefacts(
        prompt,
        artefacts,
        top_n=1,
        context_need=profile.need,
        context_profile=profile,
        enabled_context_tools=plan_tool_names(choose_preparation_plan(profile, prompt)),
    )

    assert selected[0].key == "file_summary:canonical.json"


def test_scheduling_queries_prefer_confirmed_invite_context():
    now = time.time()
    artefacts = [
        Artefact(
            key="file_summary:generic-deepdive.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="gmail/team/generic-deepdive.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="Technical deep dive coordination for hosted integration scheduler.",
        ),
        Artefact(
            key="file_summary:confirmed-private-review.json",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="gmail/solutions/architecture-review-booking.json",
            source_mtime=now,
            generated_at=now,
            model="test",
            content=(
                "Schedule private hosting architecture review. Confirming Tue Oct 24, 10:00-11:15 PT. "
                "Invite attached. 60-90 minute architecture review / technical deep dive for private VPC deployment."
            ),
        ),
    ]
    prompt = (
        "When is the 60 to 90 minute technical deep dive scheduled with the healthcare client "
        "about running model serving inside their own isolated network, and what is the time window in Pacific time?"
    )
    profile = infer_context_preparation_profile(prompt)

    selected = select_artefacts(
        prompt,
        artefacts,
        top_n=1,
        context_need=profile.need,
        context_profile=profile,
        enabled_context_tools=plan_tool_names(choose_preparation_plan(profile, prompt)),
    )

    assert selected[0].key == "file_summary:confirmed-private-review.json"


def test_context_specific_rank_boosts_require_enabled_policy_tools():
    now = time.time()
    prompt = "When is the technical deep dive scheduled with the customer?"
    profile = infer_context_preparation_profile(prompt)
    artefact = Artefact(
        key="file_summary:confirmed.json",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="gmail/solutions/architecture-review-booking.json",
        source_mtime=now,
        generated_at=now,
        model="test",
        content="Confirming Tue Oct 24, 10:00-11:15 PT. Invite attached.",
    )
    factors_without_policy: dict[str, list] = {}
    factors_with_policy: dict[str, list] = {}

    select_artefacts(
        prompt,
        [artefact],
        top_n=1,
        context_need=profile.need,
        context_profile=profile,
        capture_factors=factors_without_policy,
    )
    select_artefacts(
        prompt,
        [artefact],
        top_n=1,
        context_need=profile.need,
        context_profile=profile,
        capture_factors=factors_with_policy,
        enabled_context_tools=plan_tool_names(choose_preparation_plan(profile, prompt)),
    )

    assert not any(factor.name == "scheduling_evidence" for factor in factors_without_policy[artefact.key])
    assert any(factor.name == "scheduling_evidence" for factor in factors_with_policy[artefact.key])


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


def test_context_profile_does_not_treat_plain_compound_fact_question_as_multi_source():
    profile = infer_context_preparation_profile(
        "What failover sequence and recovery targets did MedThink specify for handling an EU region outage?"
    )

    assert profile.need != "multi_source_synthesis"


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
