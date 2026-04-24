# SPDX-License-Identifier: Apache-2.0
"""WS6 Track C — file edit between cycles invalidates a persisted briefing.

This is the end-to-end invariant the 0.8.0 persistence work was built for:

    "Why would we continuously delete the things we've prepared unless
    we're certain they're no longer relevant?" — user, 2026-04-23

Inverted: *when a file actually changes, the prediction that leaned on
that file should give up its briefing (and possibly stale) — but only
then.* Two consecutive ``precompute_cycle`` calls on the same engine
with a file edit in between must:

    1. Carry a populated ``prepared_briefing`` out of cycle 1.
    2. Capture per-path ``file_content_hashes`` for the files that
       briefing drew evidence from.
    3. Observe the file edit via :func:`read_content_hashes` at the top
       of cycle 2.
    4. Fire the ``file_change`` invalidation signal via
       :meth:`PredictionRegistry.apply_invalidation_signals`.
    5. Clear the ``prepared_briefing`` (and ``draft_answer``) and record
       an ``invalidation_reason`` containing ``file_change``.
    6. Either demote the prediction's weight (if it still clears the
       starvation floor) or stale it (if the post-decay weight falls
       below ``MIN_FLOOR_WEIGHT``).

If this test ever fails, the 0.8.0 persistence promise has regressed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter


async def _stub_llm(_prompt: str) -> str:
    return '{"ranked_files": ["src/parser.py"], "semantic_intent": "probe parser", "confidence": 0.7, "follow_on": []}'


def _seed_repo(repo: Path) -> None:
    for sub in ("src", "tests", "docs"):
        (repo / sub).mkdir(exist_ok=True)
    (repo / "src" / "parser.py").write_text("def parse(x):\n    return x\n")
    (repo / "src" / "handler.py").write_text("def handle(x):\n    return x\n")
    (repo / "tests" / "test_parser.py").write_text("def test_parse():\n    pass\n")
    (repo / "docs" / "README.md").write_text("# API\n")


def _make_engine(repo_root: Path) -> VanerEngine:
    engine = VanerEngine(adapter=CodeRepoAdapter(repo_root), llm=_stub_llm)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "all"
    return engine


@pytest.mark.asyncio
async def test_file_change_between_cycles_invalidates_ready_briefing(temp_repo: Path):
    _seed_repo(temp_repo)
    engine = _make_engine(temp_repo)
    await engine.initialize()

    for q in ["implement parser", "add tests for parser"]:
        engine._arc_model.observe(q)
        await engine.store.insert_query_history(
            session_id="s",
            query_text=q,
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
        )

    # Cycle 1 — precompute, find a prediction that reached a populated briefing.
    await engine.precompute_cycle()
    predictions_c1 = engine.get_active_predictions()
    hashed_predictions = [p for p in predictions_c1 if p.artifacts.file_content_hashes]
    assert hashed_predictions, (
        "cycle 1 produced no predictions with captured file_content_hashes — "
        "the briefing-synthesis path never attached any hashes, so the "
        "invalidation sweep has nothing to compare against"
    )
    target = hashed_predictions[0]
    before_hashes = dict(target.artifacts.file_content_hashes)
    before_briefing = target.artifacts.prepared_briefing
    before_weight = target.run.weight
    assert before_briefing is not None or target.run.readiness in {
        "evidence_gathering",
        "drafting",
        "ready",
    }, "baseline: prediction should have meaningful state after cycle 1"

    # Identify a watched file and edit it on disk to trigger file_change.
    edited_path = next(iter(before_hashes.keys()))
    full_path = temp_repo / edited_path
    original = full_path.read_text()
    full_path.write_text(original + "\n# edited between cycles — invalidate me\n")

    # Cycle 2 — invalidation sweep runs first, then the merge.
    await engine.precompute_cycle()
    after = engine._prediction_registry.get(target.spec.id)
    assert after is not None, "prediction should still exist in the registry"

    if after.run.readiness == "stale":
        # Demotion pushed the weight below MIN_FLOOR_WEIGHT; stale is correct.
        assert "file_change" in after.run.invalidation_reason
    else:
        # Non-stale path: weight demoted, briefing cleared, reason recorded.
        assert after.run.weight < before_weight, (
            f"expected weight demotion after file edit; before={before_weight} after={after.run.weight}"
        )
        assert after.artifacts.prepared_briefing is None, "prepared_briefing should be cleared when the underlying file changed"
        assert after.artifacts.draft_answer is None, "draft_answer should be cleared when its evidence moved on disk"
        assert "file_change" in after.run.invalidation_reason, (
            f"expected file_change in invalidation_reason; got: {after.run.invalidation_reason!r}"
        )
        # The captured hash for the edited path should now reflect the new file.
        assert after.artifacts.file_content_hashes.get(edited_path) != before_hashes[edited_path], (
            "captured hash for the edited path should have been refreshed; otherwise the next cycle would re-trigger the same signal"
        )


@pytest.mark.asyncio
async def test_no_file_change_preserves_briefing_across_cycles(temp_repo: Path):
    """Companion invariant to the file-change case: when no signal fires, the
    briefing must NOT be cleared. Without this the 'no wall-clock decay'
    contract is silently broken and every cycle becomes a rebuild."""
    _seed_repo(temp_repo)
    engine = _make_engine(temp_repo)
    await engine.initialize()

    for q in ["implement parser", "add tests for parser"]:
        engine._arc_model.observe(q)
        await engine.store.insert_query_history(
            session_id="s",
            query_text=q,
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
        )

    await engine.precompute_cycle()
    predictions_c1 = {p.id: p for p in engine.get_active_predictions()}
    with_briefing_c1 = {pid: p.artifacts.prepared_briefing for pid, p in predictions_c1.items() if p.artifacts.prepared_briefing}
    assert with_briefing_c1, "cycle 1 should produce at least one briefing to check"

    # No file edits, no commits, no category change — cycle 2 should preserve state.
    await engine.precompute_cycle()
    for pid, briefing_c1 in with_briefing_c1.items():
        after = engine._prediction_registry.get(pid)
        assert after is not None, f"prediction {pid} unexpectedly dropped"
        assert after.artifacts.prepared_briefing == briefing_c1, (
            f"briefing for {pid} changed without any invalidation signal — this breaks the WS6 no-wall-clock-decay contract"
        )
        assert "file_change" not in after.run.invalidation_reason
