# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    ReadinessState,
    prediction_id,
)
from vaner.intent.readiness import (
    eta_bucket,
    eta_bucket_label,
    is_adoptable,
    readiness_label,
    suppression_reason,
)


def _prompt(
    *,
    readiness: ReadinessState = "queued",
    scenarios_complete: int = 0,
    scenarios_spawned: int = 0,
    tokens_used: int = 0,
    token_budget: int = 2048,
    briefing: str | None = None,
    draft: str | None = None,
    spent: bool = False,
) -> PredictedPrompt:
    spec = PredictionSpec(
        id=prediction_id("arc", "anchor", "label"),
        label="Draft the project update",
        description="Suspected next move",
        source="arc",
        anchor="anchor",
        confidence=0.72,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    run = PredictionRun(
        weight=0.5,
        token_budget=token_budget,
        tokens_used=tokens_used,
        scenarios_spawned=scenarios_spawned,
        scenarios_complete=scenarios_complete,
        readiness=readiness,
        updated_at=0.0,
        spent=spent,
    )
    artifacts = PredictionArtifacts(
        prepared_briefing=briefing,
        draft_answer=draft,
    )
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


# ---------- readiness_label -------------------------------------------------


def test_readiness_labels_are_title_case_strings() -> None:
    assert readiness_label("queued") == "Queued"
    assert readiness_label("grounding") == "Grounding"
    assert readiness_label("evidence_gathering") == "Gathering evidence"
    assert readiness_label("drafting") == "Drafting"
    assert readiness_label("ready") == "Ready"
    assert readiness_label("stale") == "Stale"


# ---------- eta_bucket ------------------------------------------------------


def test_ready_state_returns_ready_now() -> None:
    assert eta_bucket(_prompt(readiness="ready")) == "ready_now"


def test_stale_returns_none() -> None:
    assert eta_bucket(_prompt(readiness="stale")) is None


def test_drafting_with_high_progress_returns_under_20s() -> None:
    p = _prompt(
        readiness="drafting",
        scenarios_spawned=5,
        scenarios_complete=4,
        tokens_used=1200,
        token_budget=2048,
    )
    assert eta_bucket(p) == "under_20s"


def test_drafting_with_low_progress_returns_under_1m() -> None:
    p = _prompt(
        readiness="drafting",
        scenarios_spawned=5,
        scenarios_complete=1,
        tokens_used=50,
    )
    assert eta_bucket(p) == "under_1m"


def test_evidence_gathering_progress_50pct_returns_under_1m() -> None:
    p = _prompt(
        readiness="evidence_gathering",
        scenarios_spawned=4,
        scenarios_complete=2,
    )
    assert eta_bucket(p) == "under_1m"


def test_evidence_gathering_early_returns_working() -> None:
    p = _prompt(
        readiness="evidence_gathering",
        scenarios_spawned=4,
        scenarios_complete=0,
    )
    assert eta_bucket(p) == "working"


def test_queued_and_grounding_return_maturing() -> None:
    assert eta_bucket(_prompt(readiness="queued")) == "maturing"
    assert eta_bucket(_prompt(readiness="grounding")) == "maturing"


def test_eta_bucket_label_maps_through() -> None:
    assert eta_bucket_label("ready_now") == "Ready now"
    assert eta_bucket_label("under_20s") == "~10–20s"
    assert eta_bucket_label(None) is None


def test_eta_ignores_out_of_bounds_counts() -> None:
    # scenarios_complete > scenarios_spawned shouldn't crash or produce garbage
    p = _prompt(
        readiness="evidence_gathering",
        scenarios_spawned=2,
        scenarios_complete=99,
    )
    bucket = eta_bucket(p)
    assert bucket in ("under_1m", "working", "maturing")


# ---------- is_adoptable ----------------------------------------------------


def test_adoptable_when_ready_with_briefing() -> None:
    assert is_adoptable(_prompt(readiness="ready", briefing="prepared summary goes here"))


def test_adoptable_when_drafting_with_draft() -> None:
    assert is_adoptable(_prompt(readiness="drafting", draft="draft content"))


def test_not_adoptable_when_no_material() -> None:
    assert not is_adoptable(_prompt(readiness="ready"))


def test_not_adoptable_when_spent() -> None:
    assert not is_adoptable(_prompt(readiness="ready", briefing="material", spent=True))


def test_not_adoptable_when_queued() -> None:
    assert not is_adoptable(_prompt(readiness="queued", briefing="irrelevant"))


def test_not_adoptable_when_blank_briefing_only() -> None:
    assert not is_adoptable(_prompt(readiness="ready", briefing="   "))


# ---------- suppression_reason ---------------------------------------------


def test_suppression_none_when_adoptable() -> None:
    p = _prompt(readiness="ready", briefing="material")
    assert suppression_reason(p) is None


def test_suppression_stale() -> None:
    assert suppression_reason(_prompt(readiness="stale")) == "stale"


def test_suppression_already_adopted() -> None:
    p = _prompt(readiness="ready", briefing="material", spent=True)
    assert suppression_reason(p) == "already_adopted"


def test_suppression_not_ready_yet() -> None:
    assert suppression_reason(_prompt(readiness="queued")) == "not_ready_yet"


def test_suppression_no_prepared_material() -> None:
    assert suppression_reason(_prompt(readiness="ready")) == "no_prepared_material"
