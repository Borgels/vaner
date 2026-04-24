# SPDX-License-Identifier: Apache-2.0
"""Tests for ``VanerEngine._grade_draft_at_serve``.

Exercises the serve-time draft quality signals (``answer_reuse_ratio`` and
``directional_correct``) so they stop being hardcoded placeholders.
"""

from __future__ import annotations

import pytest


def _make_engine(tmp_path, *, embed=None):
    from vaner.cli.commands.config import load_config
    from vaner.engine import VanerEngine
    from vaner.intent.adapter import CodeRepoAdapter

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# test\n")
    config = load_config(repo)
    return VanerEngine(
        adapter=CodeRepoAdapter(repo),
        config=config,
        llm=None,
        embed=embed,
    )


@pytest.mark.asyncio
async def test_reuse_ratio_perfect_overlap(tmp_path):
    engine = _make_engine(tmp_path)
    reuse, _ = await engine._grade_draft_at_serve(
        prompt="test",
        predicted_prompt="test",
        draft_referenced_paths={"a.py", "b.py", "c.py"},
        served_paths={"a.py", "b.py", "c.py"},
    )
    assert reuse == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_reuse_ratio_partial_overlap(tmp_path):
    engine = _make_engine(tmp_path)
    reuse, _ = await engine._grade_draft_at_serve(
        prompt="x",
        predicted_prompt="y",
        draft_referenced_paths={"a.py", "b.py"},
        served_paths={"b.py", "c.py"},
    )
    # intersection={b}, union={a,b,c} → 1/3
    assert reuse == pytest.approx(1 / 3, abs=1e-6)


@pytest.mark.asyncio
async def test_reuse_ratio_disjoint(tmp_path):
    engine = _make_engine(tmp_path)
    reuse, _ = await engine._grade_draft_at_serve(
        prompt="x",
        predicted_prompt="y",
        draft_referenced_paths={"a.py"},
        served_paths={"b.py"},
    )
    assert reuse == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_directional_false_without_embed_on_different_prompts(tmp_path):
    engine = _make_engine(tmp_path, embed=None)
    _, directional = await engine._grade_draft_at_serve(
        prompt="configure the database migration schema",
        predicted_prompt="render the homepage banner",
        draft_referenced_paths=set(),
        served_paths=set(),
    )
    assert directional is False


@pytest.mark.asyncio
async def test_directional_true_without_embed_on_similar_prompts(tmp_path):
    engine = _make_engine(tmp_path, embed=None)
    _, directional = await engine._grade_draft_at_serve(
        prompt="configure the database migration schema",
        predicted_prompt="migrate the database schema configuration",
        draft_referenced_paths=set(),
        served_paths=set(),
    )
    # Token Jaccard fallback: {configure,database,migration,schema,the} vs
    # {migrate,database,schema,configuration,the} → intersection {database,schema,the}=3,
    # union=7 → 0.43 >= 0.40
    assert directional is True


@pytest.mark.asyncio
async def test_directional_true_with_embed_on_cosine_match(tmp_path):
    async def fake_embed(texts):
        # Both prompts map to the same vector → cosine = 1.0 >= 0.70
        return [[1.0, 0.0, 0.0] for _ in texts]

    engine = _make_engine(tmp_path, embed=fake_embed)
    _, directional = await engine._grade_draft_at_serve(
        prompt="totally different words",
        predicted_prompt="nothing in common",
        draft_referenced_paths=set(),
        served_paths=set(),
    )
    assert directional is True


@pytest.mark.asyncio
async def test_directional_false_with_embed_on_orthogonal_vectors(tmp_path):
    async def fake_embed(texts):
        # First prompt: [1,0,0]; second: [0,1,0]. Cosine = 0.0 < 0.70
        if texts[0] == "orthogonal prompt":
            return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        return [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]

    engine = _make_engine(tmp_path, embed=fake_embed)
    _, directional = await engine._grade_draft_at_serve(
        prompt="orthogonal prompt",
        predicted_prompt="different thing",
        draft_referenced_paths=set(),
        served_paths=set(),
    )
    # Cosine is 0.0, and the token Jaccard fallback is also 0 → False
    assert directional is False


@pytest.mark.asyncio
async def test_empty_prompts_not_directional(tmp_path):
    engine = _make_engine(tmp_path)
    _, directional = await engine._grade_draft_at_serve(
        prompt="",
        predicted_prompt="",
        draft_referenced_paths=set(),
        served_paths=set(),
    )
    assert directional is False


@pytest.mark.asyncio
async def test_embed_exception_falls_back_to_tokens(tmp_path):
    async def failing_embed(texts):
        raise RuntimeError("embed service unavailable")

    engine = _make_engine(tmp_path, embed=failing_embed)
    _, directional = await engine._grade_draft_at_serve(
        prompt="configure database schema migration",
        predicted_prompt="configure database schema migration",
        draft_referenced_paths=set(),
        served_paths=set(),
    )
    # Exception path → fall back to token Jaccard (identical strings → 1.0)
    assert directional is True
