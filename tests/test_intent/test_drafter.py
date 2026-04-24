# SPDX-License-Identifier: Apache-2.0
"""WS10 — Drafter tests.

Gate arithmetic and the rewrite+draft pipeline in isolation. The end-to-end
behaviour through ``_precompute_predicted_responses`` is still exercised
by ``test_predicted_response.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from vaner.intent.briefing import BriefingAssembler
from vaner.intent.drafter import Drafter, DraftResult
from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    prediction_id,
)


def _prompt() -> PredictedPrompt:
    spec = PredictionSpec(
        id=prediction_id("pattern", "add tests", "Recurring: add tests"),
        label="Recurring: add tests",
        description="User has added tests after implementation 4x recently",
        source="pattern",
        anchor="add tests",
        confidence=0.72,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    run = PredictionRun(weight=0.6, token_budget=2048, scenarios_complete=2, updated_at=0.0)
    artifacts = PredictionArtifacts(evidence_score=1.5)
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


# ---------------------------------------------------------------------------
# Gate arithmetic
# ---------------------------------------------------------------------------


def test_passes_gates_accepts_healthy_inputs():
    drafter = Drafter(llm=None, assembler=BriefingAssembler())
    assert drafter.passes_gates(
        posterior_confidence=0.7,
        evidence_quality=0.6,
        evidence_volatility=0.2,
        prior_draft_usefulness=0.1,
        has_budget=True,
        gates={
            "draft_posterior_threshold": 0.55,
            "draft_evidence_threshold": 0.45,
            "draft_volatility_ceiling": 0.40,
        },
    )


@pytest.mark.parametrize(
    "kwarg,value",
    [
        ("posterior_confidence", 0.3),  # below threshold
        ("evidence_quality", 0.2),  # below threshold
        ("evidence_volatility", 0.9),  # above ceiling
        ("prior_draft_usefulness", -0.1),  # negative
        ("has_budget", False),  # no budget
    ],
)
def test_passes_gates_rejects_each_failure_mode(kwarg, value):
    drafter = Drafter(llm=None, assembler=BriefingAssembler())
    inputs = {
        "posterior_confidence": 0.7,
        "evidence_quality": 0.6,
        "evidence_volatility": 0.2,
        "prior_draft_usefulness": 0.1,
        "has_budget": True,
        "gates": {},
    }
    inputs[kwarg] = value
    assert not drafter.passes_gates(**inputs)


# ---------------------------------------------------------------------------
# LLM pipeline
# ---------------------------------------------------------------------------


def test_draft_for_prediction_returns_none_without_llm():
    drafter = Drafter(llm=None, assembler=BriefingAssembler())
    result = asyncio.run(
        drafter.draft_for_prediction(
            _prompt(),
            candidate_prompt="add unit tests",
            category="testing",
            recent_queries=[],
            file_summaries=[],
            available_paths=[],
        )
    )
    assert result is None


def test_draft_for_prediction_reuses_rewrite_when_provided():
    # Count how many times the LLM is called — with reuse_rewrite supplied
    # the rewrite stage is skipped, so we expect exactly 1 call (draft stage only).
    calls: list[str] = []

    async def _llm(prompt: str) -> str:
        calls.append(prompt)
        return "draft body"

    drafter = Drafter(llm=_llm, assembler=BriefingAssembler())
    result = asyncio.run(
        drafter.draft_for_prediction(
            _prompt(),
            candidate_prompt="original candidate",
            category="testing",
            recent_queries=["previous query"],
            file_summaries=["- src/parser.py: parse()"],
            available_paths=["src/parser.py"],
            reuse_rewrite="cached canonical prompt",
        )
    )
    assert result is not None
    assert len(calls) == 1  # rewrite skipped
    assert result.predicted_prompt == "cached canonical prompt"
    assert result.draft_answer == "draft body"
    assert result.briefing is not None
    assert result.briefing.text  # not empty


def test_draft_for_prediction_runs_both_stages_otherwise():
    responses = iter(["canonicalised prompt", "draft body"])
    calls: list[str] = []

    async def _llm(prompt: str) -> str:
        calls.append(prompt)
        return next(responses)

    drafter = Drafter(llm=_llm, assembler=BriefingAssembler())
    result = asyncio.run(
        drafter.draft_for_prediction(
            _prompt(),
            candidate_prompt="add unit tests",
            category="testing",
            recent_queries=["write a parser"],
            file_summaries=["- src/parser.py: parse()"],
            available_paths=["src/parser.py"],
        )
    )
    assert result is not None
    assert len(calls) == 2  # rewrite + draft
    assert result.predicted_prompt == "canonicalised prompt"
    assert result.draft_answer == "draft body"


def test_draft_for_prediction_falls_back_when_rewrite_fails():
    # Rewrite call raises; drafter should keep the original candidate and
    # continue to the draft stage. This preserves the pre-WS10 inline
    # behaviour where a rewrite failure wasn't fatal.
    stage = {"n": 0}

    async def _llm(prompt: str) -> str:
        stage["n"] += 1
        if stage["n"] == 1:
            raise RuntimeError("rewrite offline")
        return "draft body"

    drafter = Drafter(llm=_llm, assembler=BriefingAssembler())
    result = asyncio.run(
        drafter.draft_for_prediction(
            _prompt(),
            candidate_prompt="add unit tests",
            category="testing",
            recent_queries=[],
            file_summaries=[],
            available_paths=[],
        )
    )
    assert result is not None
    assert result.predicted_prompt == "add unit tests"
    assert result.draft_answer == "draft body"


def test_draft_for_prediction_returns_none_on_draft_failure():
    async def _llm(_prompt: str) -> str:
        raise RuntimeError("draft model down")

    drafter = Drafter(llm=_llm, assembler=BriefingAssembler())
    result = asyncio.run(
        drafter.draft_for_prediction(
            _prompt(),
            candidate_prompt="x",
            category="testing",
            recent_queries=[],
            file_summaries=[],
            available_paths=[],
            reuse_rewrite="y",  # skip rewrite; the only call is draft, which fails
        )
    )
    assert result is None


def test_draft_for_prediction_returns_none_on_empty_draft():
    async def _llm(_prompt: str) -> str:
        return "   "

    drafter = Drafter(llm=_llm, assembler=BriefingAssembler())
    result = asyncio.run(
        drafter.draft_for_prediction(
            _prompt(),
            candidate_prompt="x",
            category="testing",
            recent_queries=[],
            file_summaries=[],
            available_paths=[],
            reuse_rewrite="y",
        )
    )
    assert result is None


def test_draft_for_prediction_respects_deadline():
    import time

    called = {"n": 0}

    async def _llm(_prompt: str) -> str:
        called["n"] += 1
        return "draft"

    drafter = Drafter(llm=_llm, assembler=BriefingAssembler())
    past = time.monotonic() - 10
    result = asyncio.run(
        drafter.draft_for_prediction(
            _prompt(),
            candidate_prompt="x",
            category="testing",
            recent_queries=[],
            file_summaries=[],
            available_paths=[],
            deadline=past,
        )
    )
    assert result is None
    assert called["n"] == 0


def test_draft_result_tokens_used_is_positive_with_content():
    async def _llm(_prompt: str) -> str:
        return "a draft response with enough tokens to register"

    drafter = Drafter(llm=_llm, assembler=BriefingAssembler())
    result = asyncio.run(
        drafter.draft_for_prediction(
            _prompt(),
            candidate_prompt="x",
            category="testing",
            recent_queries=[],
            file_summaries=[],
            available_paths=[],
            reuse_rewrite="y",
        )
    )
    assert result is not None
    assert result.tokens_used > 0


def test_draft_result_is_typed_and_populates_metadata():
    async def _llm(_prompt: str) -> str:
        return "draft body"

    drafter = Drafter(llm=_llm, assembler=BriefingAssembler())
    result = asyncio.run(
        drafter.draft_for_prediction(
            _prompt(),
            candidate_prompt="x",
            category="testing",
            recent_queries=[],
            file_summaries=[],
            available_paths=[],
            reuse_rewrite="y",
        )
    )
    assert isinstance(result, DraftResult)
    assert result.metadata["category"] == "testing"
    assert result.metadata["source"] == "pattern"
