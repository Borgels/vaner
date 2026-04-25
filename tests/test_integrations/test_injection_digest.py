# SPDX-License-Identifier: Apache-2.0

from vaner.integrations.injection import DigestEntry, build_digest, count_tokens


def _entry(label: str, readiness: str = "Ready", **kw) -> DigestEntry:
    return DigestEntry(label=label, readiness_label=readiness, **kw)


def test_empty_input_returns_empty_string() -> None:
    assert build_digest([], budget_tokens=500) == ""


def test_zero_budget_returns_empty_string() -> None:
    assert build_digest([_entry("foo")], budget_tokens=0) == ""


def test_contains_structural_markers_and_entry() -> None:
    out = build_digest(
        [_entry("Draft the project update", readiness="Ready")],
        budget_tokens=500,
    )
    assert '<VANER_PREPARED_WORK_DIGEST version="1">' in out
    assert "</VANER_PREPARED_WORK_DIGEST>" in out
    assert "Draft the project update" in out
    assert "[Ready]" in out


def test_budget_cap_is_respected() -> None:
    entries = [_entry(f"prediction {i}") for i in range(10)]
    out = build_digest(entries, budget_tokens=120)
    # Default counter is len(text)//4 — so 120 tokens ≈ 480 chars.
    assert count_tokens(out) <= 120


def test_lower_ranked_entries_dropped_under_budget() -> None:
    entries = [
        _entry("first high-value prediction with a fairly long descriptive label"),
        _entry("second lower-value prediction with another long label"),
        _entry("third prediction with a descriptive label"),
        _entry("fourth"),
    ]
    # ~100 tokens fits the framing + 1-2 entries but not all four.
    tight = build_digest(entries, budget_tokens=100)
    assert "first high-value prediction" in tight
    assert "fourth" not in tight, "tight budget should drop lower-ranked entries"
    # Roomy budget includes everything.
    roomy = build_digest(entries, budget_tokens=1000)
    assert "fourth" in roomy


def test_confidence_details_hidden_by_default() -> None:
    out = build_digest(
        [_entry("summarize the paper", evidence_score=0.82)],
        budget_tokens=500,
    )
    assert "Evidence:" not in out


def test_confidence_details_emitted_when_requested() -> None:
    out = build_digest(
        [_entry("summarize the paper", evidence_score=0.82)],
        budget_tokens=500,
        include_confidence_details=True,
    )
    assert "Evidence: 0.82" in out


def test_max_entries_caps_rendering() -> None:
    entries = [_entry(f"prediction {i}") for i in range(10)]
    out = build_digest(entries, budget_tokens=500, max_entries=3)
    assert out.count("\n1.") == 1
    assert out.count("\n2.") == 1
    assert out.count("\n3.") == 1
    assert out.count("\n4.") == 0


def test_eta_bucket_label_distinct_from_readiness_shown() -> None:
    out = build_digest(
        [_entry("foo", readiness="Drafting", eta_bucket_label="~20s")],
        budget_tokens=500,
    )
    assert "[Drafting]" in out
    assert "~20s" in out
