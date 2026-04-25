# SPDX-License-Identifier: Apache-2.0
"""Auto-wire a production :class:`MaturationDrafterCallable` for background
refinement when :attr:`RefinementConfig.enabled` is True and no drafter
has been manually injected.

0.8.5 WS11: flipped `refinement.enabled` to True by default. This module
is the glue that actually makes that flip produce work — without it the
engine sees ``enabled=True`` but ``_refinement_drafter is None`` and
the maturation pass is a no-op.

The wiring reuses the same :class:`Drafter` instance the cycle's
in-process drafting uses. Maturation drafting reuses the backend LLM
callable but wraps the prompt frame so the candidate draft is
*different from* the existing draft (not a re-draft of the same
material).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from vaner.intent.deep_run_maturation import MaturationContract
from vaner.intent.prediction import PredictedPrompt

if TYPE_CHECKING:
    from vaner.intent.drafter import Drafter


def build_production_maturation_drafter(
    drafter: Drafter | None,
    *,
    llm: Callable[[str], Awaitable[str]] | None = None,
) -> Callable[[PredictedPrompt, MaturationContract], Awaitable[tuple[str, list[str]]]] | None:
    """Return a MaturationDrafterCallable that wraps *drafter*, or None.

    Returns None (refinement stays a no-op) when no LLM is available —
    the engine's existing ``_refinement_drafter is None`` gate catches it
    cleanly and no work is attempted.
    """
    if drafter is None and llm is None:
        return None

    llm_callable = llm
    if llm_callable is None and drafter is not None:
        llm_callable = getattr(drafter, "_llm", None)
    if llm_callable is None:
        return None

    async def _mature_draft(
        prediction: PredictedPrompt,
        contract: MaturationContract,
    ) -> tuple[str, list[str]]:
        """Produce a candidate new draft for a maturation pass.

        Wraps the backend LLM with a prompt frame that explicitly
        requests a *different* draft improving on the existing one
        against the contract's acceptance clauses. Evidence refs are
        extracted from the response by a simple `[ref: …]` pattern the
        prompt instructs the model to use; the judge still does the
        authoritative improvement grade.
        """
        existing_draft = prediction.artifacts.draft_answer or ""
        briefing = prediction.artifacts.prepared_briefing or ""
        intent = prediction.spec.label
        contract_text = _render_contract_clauses(contract)

        prompt = _build_prompt(
            intent=intent,
            briefing=briefing,
            existing_draft=existing_draft,
            contract=contract_text,
        )
        response = await llm_callable(prompt)
        draft_text, evidence_refs = _parse_response(response)
        return draft_text, evidence_refs

    return _mature_draft


def _render_contract_clauses(contract: MaturationContract) -> str:
    """Stringify the contract's acceptance clauses for the prompt frame."""
    lines: list[str] = []
    clauses = getattr(contract, "improvement_clauses", None) or []
    for clause in clauses:
        lines.append(f"- {clause}")
    if not lines:
        return "(no explicit improvement clauses — produce a materially better draft)"
    return "\n".join(lines)


def _build_prompt(
    *,
    intent: str,
    briefing: str,
    existing_draft: str,
    contract: str,
) -> str:
    # Explicit, minimal frame. The production Drafter uses a richer
    # prompt; for maturation we ask the model to improve specifically
    # along the contract axes rather than re-draft from scratch.
    return (
        "You are producing an IMPROVED draft for a predicted user prompt.\n"
        "Your task: return a draft that is *materially better* than the existing one,\n"
        "specifically addressing the acceptance clauses below. Keep the same intent.\n"
        "Cite evidence inline using [ref: <short-name>] markers; the judge will\n"
        "compare against the existing evidence set.\n\n"
        f"Intent: {intent}\n\n"
        "Prepared briefing:\n"
        f"{briefing}\n\n"
        "Existing draft:\n"
        f"{existing_draft}\n\n"
        "Acceptance clauses — the new draft MUST satisfy each:\n"
        f"{contract}\n\n"
        "Return ONLY the new draft text. Inline [ref: …] markers are encouraged."
    )


def _parse_response(response: str) -> tuple[str, list[str]]:
    """Extract ``[ref: name]`` markers from the response.

    Returns ``(draft_text, evidence_refs)``. The draft text is the
    response verbatim (the judge compares old vs new, so keep markers in
    the text for audit). Evidence refs are the deduplicated set of
    `name` values extracted from `[ref: name]`.
    """
    import re

    text = response.strip()
    refs: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\[ref:\s*([^\]]+)\]", text):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            refs.append(name)
    return text, refs
