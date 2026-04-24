# SPDX-License-Identifier: Apache-2.0
"""WS6 — invalidation-signal tests.

Companion to ``test_prediction_persistence.py``. Persistence proves the
registry keeps state across cycles; invalidation proves it gives state
up when — and only when — a signal says the underlying evidence has
moved. Together they encode the WS6 contract.
"""

from __future__ import annotations

from vaner.intent.invalidation import (
    InvalidationSignal,
    build_category_shift_signal,
    build_commit_signal,
    build_file_change_signal,
)
from vaner.intent.prediction import PredictionSpec, prediction_id
from vaner.intent.prediction_registry import PredictionRegistry

# ---------------------------------------------------------------------------
# Signal builders
# ---------------------------------------------------------------------------


def test_file_change_signal_returns_none_when_hashes_match():
    old = {"src/a.py": "aaa", "src/b.py": "bbb"}
    sig = build_file_change_signal(old, old)
    assert sig is None


def test_file_change_signal_detects_changed_path():
    old = {"src/a.py": "aaa", "src/b.py": "bbb"}
    new = {"src/a.py": "aaa-changed", "src/b.py": "bbb"}
    sig = build_file_change_signal(old, new)
    assert sig is not None
    assert sig.kind == "file_change"
    assert sig.payload["changed_paths"] == ["src/a.py"]


def test_file_change_signal_detects_deleted_path():
    old = {"src/a.py": "aaa", "src/b.py": "bbb"}
    new = {"src/a.py": "aaa"}  # b.py deleted
    sig = build_file_change_signal(old, new)
    assert sig is not None
    assert "src/b.py" in sig.payload["changed_paths"]


def test_file_change_signal_ignores_new_paths():
    """New paths in ``new_hashes`` that weren't captured before are not a
    change from the old snapshot's point of view — they'll be captured at
    the next briefing-synthesis time."""
    old = {"src/a.py": "aaa"}
    new = {"src/a.py": "aaa", "src/newfile.py": "zzz"}
    sig = build_file_change_signal(old, new)
    assert sig is None


def test_commit_signal_fires_on_head_move():
    sig = build_commit_signal("old-sha-1234567890ab", "new-sha-fedcba098765")
    assert sig is not None
    assert sig.kind == "commit"
    assert sig.payload["from_sha"] == "old-sha-1234567890ab"
    assert sig.payload["to_sha"] == "new-sha-fedcba098765"


def test_commit_signal_is_none_when_head_unchanged():
    sig = build_commit_signal("abc", "abc")
    assert sig is None


def test_commit_signal_is_none_on_empty_new_sha():
    """No git repo → no signal."""
    sig = build_commit_signal("", "")
    assert sig is None


def test_category_shift_signal_fires_on_streak():
    # 3 trailing 'debugging' preceded by 'understanding'.
    cats = ["understanding", "understanding", "debugging", "debugging", "debugging"]
    sig = build_category_shift_signal(cats, streak_threshold=3)
    assert sig is not None
    assert sig.payload["from"] == "understanding"
    assert sig.payload["to"] == "debugging"
    assert sig.payload["streak"] == 3


def test_category_shift_signal_none_on_short_list():
    sig = build_category_shift_signal(["a", "b"], streak_threshold=3)
    assert sig is None


def test_category_shift_signal_none_when_streak_not_uniform():
    cats = ["a", "a", "a", "b", "c", "d"]
    sig = build_category_shift_signal(cats, streak_threshold=3)
    assert sig is None


# ---------------------------------------------------------------------------
# Registry.apply_invalidation_signals
# ---------------------------------------------------------------------------


def _spec(
    *,
    source: str = "arc",
    anchor: str = "understanding",
    label: str = "understand code",
    specificity: str = "concrete",
    confidence: float = 0.7,
) -> PredictionSpec:
    return PredictionSpec(
        id=prediction_id(source, anchor, label),
        label=label,
        description="",
        source=source,  # type: ignore[arg-type]
        anchor=anchor,
        confidence=confidence,
        hypothesis_type="likely_next",
        specificity=specificity,  # type: ignore[arg-type]
        created_at=0.0,
    )


def test_file_change_clears_briefing_and_demotes_weight():
    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = _spec()
    reg.merge([spec], cycle_n=1)
    reg.attach_scenario(spec.id, "scen-1")
    reg.attach_artifact(
        spec.id,
        briefing="## Context\nparser files",
        draft="tentative answer",
        file_content_hashes={"src/parser.py": "hash-v1", "src/handler.py": "hash-v1"},
    )
    before = reg.get(spec.id)
    assert before is not None
    assert before.artifacts.prepared_briefing is not None
    assert before.artifacts.draft_answer == "tentative answer"
    initial_weight = before.run.weight

    # File changed on disk.
    sig = InvalidationSignal(
        kind="file_change",
        payload={
            "changed_paths": ["src/parser.py"],
            "new_hashes": {"src/parser.py": "hash-v2", "src/handler.py": "hash-v1"},
        },
    )
    outcomes = reg.apply_invalidation_signals([sig])
    after = reg.get(spec.id)
    assert after is not None
    # Briefing + draft cleared (evidence must be re-derived).
    assert after.artifacts.prepared_briefing is None
    assert after.artifacts.draft_answer is None
    # Weight demoted.
    assert after.run.weight < initial_weight
    # Captured hash refreshed to current.
    assert after.artifacts.file_content_hashes["src/parser.py"] == "hash-v2"
    # invalidation_reason records the event.
    assert "file_change" in after.run.invalidation_reason
    assert outcomes[spec.id] in {"cleared_briefing", "staled"}


def test_file_change_stales_when_weight_collapses_below_floor():
    """Repeated file_change halvings eventually push weight below the
    ``MIN_FLOOR_WEIGHT`` — the prediction stales out."""
    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = _spec()
    reg.merge([spec], cycle_n=1)
    reg.attach_artifact(
        spec.id,
        briefing="b",
        file_content_hashes={"src/a.py": "v1"},
    )

    # Halve repeatedly. Starting weight is 1.0 (single-spec merge), floor
    # is 0.05. Five halvings puts weight at 0.03125, below floor.
    for i in range(6):
        sig = InvalidationSignal(
            kind="file_change",
            payload={
                "changed_paths": ["src/a.py"],
                "new_hashes": {"src/a.py": f"v{i + 2}"},
            },
        )
        # Re-attach briefing each round so the signal has something to clear.
        reg.attach_artifact(spec.id, briefing="b", file_content_hashes={"src/a.py": f"v{i + 2}"})
        reg.apply_invalidation_signals([sig])
    final = reg.get(spec.id)
    assert final is not None
    # Prediction should now be staled; confirm it's no longer active.
    assert final.run.readiness == "stale" or final not in reg.active()


def test_commit_stales_phase_anchored_predictions_only():
    """Commit signal stales non-concrete predictions; concrete ones are
    left for the file_change signal to handle."""
    reg = PredictionRegistry(cycle_token_pool=10_000)
    category_spec = _spec(
        source="history",
        anchor="debugging",
        label="Continue: debugging",
        specificity="category",
    )
    concrete_spec = _spec(
        source="pattern",
        anchor="add tests for parser",
        label="Recurring: add tests",
        specificity="concrete",
    )
    reg.merge([category_spec, concrete_spec], cycle_n=1)

    sig = InvalidationSignal(
        kind="commit",
        payload={"from_sha": "aaa", "to_sha": "bbb"},
    )
    reg.apply_invalidation_signals([sig])

    category_after = reg.get(category_spec.id)
    concrete_after = reg.get(concrete_spec.id)
    assert category_after is not None and concrete_after is not None
    assert category_after.run.readiness == "stale"
    assert concrete_after.run.readiness != "stale"


def test_category_shift_demotes_anchored_predictions():
    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = _spec(
        source="arc",
        anchor="understanding",
        label="Explore code",
        specificity="category",
    )
    reg.merge([spec], cycle_n=1)
    before_weight = reg.get(spec.id).run.weight  # type: ignore[union-attr]

    sig = InvalidationSignal(
        kind="category_shift",
        payload={"from": "understanding", "to": "debugging", "streak": 3},
    )
    reg.apply_invalidation_signals([sig])

    after = reg.get(spec.id)
    assert after is not None
    assert after.run.weight < before_weight
    assert "category_shift" in after.run.invalidation_reason


def test_adoption_signal_marks_spent():
    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = _spec()
    reg.merge([spec], cycle_n=1)

    sig = InvalidationSignal(kind="adoption", payload={"prediction_id": spec.id})
    outcomes = reg.apply_invalidation_signals([sig])
    assert outcomes[spec.id] == "spent"
    prompt = reg.get(spec.id)
    assert prompt is not None
    assert prompt.run.spent is True


def test_apply_invalidation_is_noop_on_empty_list():
    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = _spec()
    reg.merge([spec], cycle_n=1)
    outcomes = reg.apply_invalidation_signals([])
    assert outcomes == {}


def test_apply_invalidation_skips_predictions_without_captured_hashes():
    """A prediction with no ``file_content_hashes`` recorded is neither
    demoted nor cleared on a file_change — the signal has nothing to
    match against."""
    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = _spec()
    reg.merge([spec], cycle_n=1)
    # No attach_artifact with hashes — captured is empty.
    before = reg.get(spec.id)
    assert before is not None
    initial_weight = before.run.weight

    sig = InvalidationSignal(
        kind="file_change",
        payload={
            "changed_paths": ["src/unrelated.py"],
            "new_hashes": {"src/unrelated.py": "zzz"},
        },
    )
    reg.apply_invalidation_signals([sig])
    after = reg.get(spec.id)
    assert after is not None
    assert after.run.weight == initial_weight
    assert after.artifacts.prepared_briefing is None  # was None before, still None
