# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, datetime

from vaner.integrations.injection import (
    AdoptedPackagePayload,
    build_adopted_package,
    count_tokens,
)

EXPIRES = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)


def _payload(**overrides) -> AdoptedPackagePayload:
    base = {
        "intent": "Draft the project update",
        "prepared_briefing": "The team finished the evidence-gathering phase yesterday.",
        "predicted_response": "Here's a draft of the update you asked for.",
        "evidence_lines": ["doc/handoff.md (latest)", "thread://slack/...", "plans/roadmap.md:42"],
        "provenance_summary": "mode=predictive_hit cache=warm freshness=fresh",
        "adopted_from_prediction_id": "pred-abc",
        "resolution_id": "adopt-pred-abc",
    }
    base.update(overrides)
    return AdoptedPackagePayload(**base)


def test_structural_markers_and_expiry() -> None:
    out = build_adopted_package(_payload(), expires_at=EXPIRES, budget_tokens=4000)
    assert '<VANER_ADOPTED_PACKAGE version="1" expires_at="2026-04-25T12:00:00+00:00">' in out
    assert "</VANER_ADOPTED_PACKAGE>" in out
    assert "Intent:" in out
    assert "Draft the project update" in out


def test_provenance_included_by_default() -> None:
    out = build_adopted_package(_payload(), expires_at=EXPIRES, budget_tokens=4000)
    assert "Provenance:" in out
    assert "mode=predictive_hit" in out
    assert "Adopted from prediction: pred-abc" in out


def test_provenance_suppressed_when_flag_off() -> None:
    out = build_adopted_package(
        _payload(),
        expires_at=EXPIRES,
        budget_tokens=4000,
        include_provenance=False,
    )
    assert "Provenance:" not in out


def test_empty_briefing_skipped() -> None:
    out = build_adopted_package(
        _payload(prepared_briefing=None),
        expires_at=EXPIRES,
        budget_tokens=4000,
    )
    assert "Prepared briefing:" not in out


def test_budget_cap_is_respected() -> None:
    long_text = "lorem ipsum dolor sit amet " * 500
    out = build_adopted_package(
        _payload(prepared_briefing=long_text, predicted_response=long_text),
        expires_at=EXPIRES,
        budget_tokens=400,
    )
    assert count_tokens(out) <= 400


def test_zero_budget_returns_empty_string() -> None:
    assert build_adopted_package(_payload(), expires_at=EXPIRES, budget_tokens=0) == ""


def test_evidence_lines_rendered_as_bullets() -> None:
    out = build_adopted_package(_payload(), expires_at=EXPIRES, budget_tokens=4000)
    assert "- doc/handoff.md (latest)" in out
    assert "- thread://slack/..." in out
