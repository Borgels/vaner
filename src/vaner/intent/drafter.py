# SPDX-License-Identifier: Apache-2.0
"""WS10 — single drafting module for every prediction source.

Prior to WS10, draft generation was split across:

- ``engine._precompute_predicted_responses`` — pattern/macro-sourced
  drafts: rewrite-prompt LLM call, drafting LLM call, cache-store, then
  registry bookkeeping — all inline in a 250-line loop.
- ``engine._process_scenario`` evidence-threshold block — arc/history-
  sourced "briefings" that reached ``ready`` with no LLM draft, just a
  path-list briefing.

This module carries exactly the drafting responsibility: given a
:class:`PredictedPrompt` and enough context to ground it (available
paths, recent queries, artefact index), run the rewrite + draft LLM
calls and assemble a :class:`DraftResult`. Readiness transitions, cache
storage, and adoption surfacing stay with the engine — this class just
produces the artefact.

Single drafter means: arc, pattern, history, and future goal-sourced
(WS7) predictions all use the same template and the same token
accounting.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from vaner.clients.llm_response import approx_tokens
from vaner.intent.briefing import Briefing, BriefingAssembler
from vaner.intent.prediction import PredictedPrompt

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class DraftResult:
    """The output of a drafting pass.

    ``predicted_prompt`` is the rewritten, canonicalised form of the
    prompt the developer is likely to send — cache entries key on this.
    ``draft_answer`` is the speculative response (None when the LLM
    returned empty / failed). ``briefing`` is the structured evidence
    package that was used to ground the draft. ``thinking`` carries any
    reasoning-mode output from the LLM when the structured-client path
    was used. ``tokens_used`` sums the prompt + completion tokens we
    observed (approximate when no tokenizer is wired in).
    """

    predicted_prompt: str
    draft_answer: str | None
    briefing: Briefing
    thinking: str = ""
    tokens_used: int = 0
    # Free-form metadata the caller can read to route downstream effects
    # (e.g. pattern macro_key for cache storage, source tag for metrics).
    metadata: dict[str, object] = field(default_factory=dict)


# ``DraftGates`` encodes the guards the pre-WS10 inline loop applied:
# posterior confidence, evidence quality, evidence volatility, draft
# usefulness prior. We keep them as a plain dict instead of a dataclass
# because callers already hold these as ``_cycle_policy_state`` entries.
DraftGates = dict[str, float]


class Drafter:
    """Generate draft responses for predictions of any source.

    Holds the shared :class:`BriefingAssembler` + references to the LLM
    callables + the cycle's draft-gate policy knobs. Construct once per
    engine lifetime and call :meth:`draft_for_prediction` per prediction
    that cleared the gates.
    """

    def __init__(
        self,
        *,
        llm: Callable[[str], Awaitable[str]] | None,
        assembler: BriefingAssembler,
    ) -> None:
        self._llm = llm
        self._assembler = assembler

    # -----------------------------------------------------------------------
    # Gate evaluation
    # -----------------------------------------------------------------------

    def passes_gates(
        self,
        *,
        posterior_confidence: float,
        evidence_quality: float,
        evidence_volatility: float,
        prior_draft_usefulness: float,
        has_budget: bool,
        gates: DraftGates,
    ) -> bool:
        """Return True when all gates are met, False to skip drafting.

        Kept as a pure function so tests can exercise the gate arithmetic
        without an LLM or a full engine.
        """
        if posterior_confidence < float(gates.get("draft_posterior_threshold", 0.55)):
            return False
        if evidence_quality < float(gates.get("draft_evidence_threshold", 0.45)):
            return False
        if evidence_volatility > float(gates.get("draft_volatility_ceiling", 0.40)):
            return False
        if prior_draft_usefulness < 0.0:
            return False
        if not has_budget:
            return False
        return True

    # -----------------------------------------------------------------------
    # Drafting
    # -----------------------------------------------------------------------

    async def draft_for_prediction(
        self,
        prompt: PredictedPrompt,
        *,
        candidate_prompt: str,
        category: str,
        recent_queries: list[str],
        file_summaries: list[str],
        available_paths: list[str],
        reuse_rewrite: str | None = None,
        deadline: float | None = None,
    ) -> DraftResult | None:
        """Run rewrite + draft LLM calls and assemble a :class:`DraftResult`.

        Parameters mirror the old inline loop's state:

        - ``candidate_prompt`` is the source string that seeds the
          rewrite (usually the pattern's example_query / macro_key, an
          arc "describe_next" label, or a goal title).
        - ``reuse_rewrite`` when supplied short-circuits Stage A: the
          rewrite LLM call is skipped and the cached rewrite is used
          instead. This is the partial-regeneration optimisation the
          inline loop had.
        - ``file_summaries`` is the list of ``"- path: snippet"`` lines
          already pulled from the artefact store; keeping this a string
          list rather than rebuilding it here preserves existing
          batching behaviour in the caller.
        - ``deadline`` is honoured: if we've already passed it, the
          method returns None without running any LLM calls.

        Returns None when the LLM is unavailable, returned empty, or
        raised — the caller should treat None as "skip this prediction"
        without counting it as a failure.
        """
        if self._llm is None:
            return None
        if deadline is not None and time.monotonic() >= deadline:
            return None

        # Stage A — rewrite the candidate prompt into a single concrete
        # sentence. Reuse rewrite from cache when available (low-volatility
        # optimisation the inline loop used).
        predicted_prompt = candidate_prompt.strip() or "(unspecified)"
        if reuse_rewrite:
            predicted_prompt = reuse_rewrite.strip()[:500]
        else:
            recent_hint = "\n".join(recent_queries[-5:]) or "(no recent queries)"
            rewrite_prompt = (
                "Rewrite the likely next developer prompt as one concrete sentence.\n"
                "Stay semantically equivalent and concise.\n\n"
                f"Candidate prompt: {predicted_prompt}\n"
                f"Recent queries:\n{recent_hint}\n\n"
                "Return plain text only."
            )
            try:
                rewritten = await self._llm(rewrite_prompt)
                if isinstance(rewritten, str) and rewritten.strip():
                    predicted_prompt = rewritten.strip()[:500]
            except Exception:
                # Rewrite failure isn't fatal — fall back to the original
                # candidate. The draft prompt below still has recent-query
                # context so it won't lose grounding.
                pass

        # Stage B — draft the response. Template intentionally mirrors
        # the pre-WS10 inline template so bench numbers stay comparable
        # while we consolidate the code path.
        summaries_text = "\n".join(file_summaries) or "(no artefact summaries available)"
        recent_hint = "\n".join(recent_queries[-5:]) or "(no recent queries)"
        draft_prompt_text = (
            "You are Vaner, a context engine drafting a speculative answer for a\n"
            "prompt the developer is likely to send next. Stay honest: if the\n"
            "evidence below is insufficient to produce a confident draft, say so\n"
            "explicitly instead of hallucinating content.\n\n"
            f"Likely next prompt (category: {category}):\n"
            f"  {predicted_prompt}\n\n"
            f"Recent developer queries for context:\n{recent_hint}\n\n"
            f"Recently touched files and their summaries:\n{summaries_text}\n\n"
            "Draft a concise response (<= 400 words) that directly addresses the\n"
            "likely prompt. Reference specific file paths, functions, or code\n"
            "lines from the summaries above when relevant. If the draft is\n"
            "speculative, prefix each uncertain claim with 'TENTATIVE:' so the\n"
            "agent consuming this draft can flag it for the user.\n\n"
            "Return the draft as plain text, no JSON, no code fences."
        )
        try:
            draft = await self._llm(draft_prompt_text)
        except Exception as exc:
            _log.debug("Drafter: draft LLM call failed for %r: %s", prompt.spec.label, exc)
            return None
        if not isinstance(draft, str) or not draft.strip():
            return None
        draft = draft.strip()

        # Build the briefing from the file-summary artefacts we had. We
        # don't have Artefact objects here — the caller already pulled
        # snippets out — so we use the simpler ``from_paths`` variant
        # and include the summary lines as the description.
        briefing = self._assembler.from_paths(
            label=prompt.spec.label,
            description=prompt.spec.description,
            paths=available_paths,
            source=prompt.spec.source,
            anchor=prompt.spec.anchor,
            confidence=prompt.spec.confidence,
            scenarios_complete=prompt.run.scenarios_complete,
            evidence_score=prompt.artifacts.evidence_score,
        )

        tokens_used = approx_tokens(draft_prompt_text) + approx_tokens(draft)

        return DraftResult(
            predicted_prompt=predicted_prompt,
            draft_answer=draft[:4000],
            briefing=briefing,
            tokens_used=tokens_used,
            metadata={
                "category": category,
                "source": prompt.spec.source,
            },
        )
