from __future__ import annotations

from vaner.memory.policy import ConflictInput, detect_conflict


def test_resolve_emits_memory_conflict_gap_when_signal_moderate() -> None:
    conflict = detect_conflict(
        ConflictInput(
            compiled_sections={"decision_digest": "auth in middleware"},
            compiled_entities={"auth", "middleware"},
            compiled_fingerprints=["a", "b"],
            fresh_entities={"auth", "routes"},
            fresh_fingerprints=["a"],
        )
    )
    assert conflict.has_conflict is True
