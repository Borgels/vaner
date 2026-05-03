# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.next_horizon import build_next_horizon_specs


def test_horizon_generates_post_plan_next_work_for_code_workspace() -> None:
    specs = build_next_horizon_specs(
        recent_queries=[
            "implement the plan",
            "run focused validation and risk checks",
            "inspect current background preparation",
        ],
        available_paths=[
            "src/vaner/engine.py",
            "src/vaner/intent/assist_decision.py",
            "tests/test_intent/test_assist_decision.py",
            "docs/release.md",
            ".github/workflows/codeql.yml",
            ".github/SECURITY.md",
            ".github/workflows/ci.yml",
        ],
        changed_paths=["src/vaner/engine.py", "tests/test_intent/test_assist_decision.py"],
        completed_plan_titles=["Turn decision first"],
        workflow_phase="stabilizing",
    )

    labels = {spec.label for spec in specs}
    assert {
        "Validate current work",
        "Review quality and risks",
        "Harden edge cases and failure modes",
        "Run static and security checks",
        "Prepare commit and release handoff",
    } <= labels
    assert any(spec.source == "horizon" for spec in specs)
    assert all(spec.structured is not None for spec in specs)
    assert any("tests/test_intent/test_assist_decision.py" in spec.structured.evidence_targets for spec in specs if spec.structured)
    assert any(".github/workflows/codeql.yml" in spec.structured.evidence_targets for spec in specs if spec.structured)


def test_horizon_is_not_code_specific_for_writing_workspace() -> None:
    specs = build_next_horizon_specs(
        recent_queries=["revise the intro", "make the argument clearer", "prepare the next draft"],
        available_paths=["drafts/essay.md", "notes/audience.md", "outline.md"],
        changed_paths=["drafts/essay.md"],
        workflow_phase="building",
    )

    labels = {spec.label for spec in specs}
    assert "Review quality and risks" in labels
    assert "Document decisions and next steps" in labels or "Continue current direction" in labels
    assert all("src/" not in " ".join(spec.structured.evidence_targets if spec.structured else ()) for spec in specs)


def test_horizon_accepts_trained_numeric_priors_without_examples() -> None:
    specs = build_next_horizon_specs(
        recent_queries=["finish the draft", "prepare next steps"],
        available_paths=["drafts/essay.md", "notes/audience.md", "outline.md"],
        changed_paths=["drafts/essay.md"],
        horizon_priors={
            "family_weights": {"document": 2.0, "validate": 0.25},
            "domain_family_boosts": {"writing": {"document": 0.10}},
            "post_plan_boosts": {"document": 0.10},
        },
        completed_plan_titles=["Draft revision"],
        workflow_phase="stabilizing",
        max_specs=3,
    )

    assert specs[0].label == "Document decisions and next steps"
    assert all("finish the draft" not in spec.anchor for spec in specs)
