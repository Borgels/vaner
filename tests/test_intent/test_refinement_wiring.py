# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from vaner.intent.deep_run_maturation import ContractClause, MaturationContract
from vaner.intent.refinement_wiring import build_production_maturation_drafter


def test_contract_renderer_includes_must_forbidden_and_intent_terms() -> None:
    from vaner.intent.refinement_wiring import _render_contract_clauses

    contract = MaturationContract(
        pass_id="p",
        target_weakness="low_evidence",
        must_clauses=(
            ContractClause(
                key="new_evidence_refs_min_2",
                description="Cite two new refs.",
                kind="must",
            ),
        ),
        forbidden_clauses=(
            ContractClause(
                key="anchor_preserved",
                description="Keep the same target.",
                kind="forbidden",
            ),
        ),
        intent_anchor="Explain ArtefactStore",
        intent_terms=("artifactstore",),
    )

    rendered = _render_contract_clauses(contract)
    assert "Original predicted user need: Explain ArtefactStore" in rendered
    assert "Preserve these concrete target terms: artifactstore" in rendered
    assert "MUST new_evidence_refs_min_2: Cite two new refs." in rendered
    assert "MUST NOT anchor_preserved: Keep the same target." in rendered


@pytest.mark.asyncio
async def test_production_maturation_drafter_prompt_carries_anti_drift_contract() -> None:
    seen_prompts: list[str] = []

    async def _llm(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "Improved ArtefactStore answer. [ref: store] [ref: tests]"

    drafter = build_production_maturation_drafter(None, llm=_llm)
    assert drafter is not None

    contract = MaturationContract(
        pass_id="p",
        target_weakness="low_evidence",
        must_clauses=(),
        forbidden_clauses=(),
        intent_anchor="Explain ArtefactStore",
        intent_terms=("artifactstore",),
    )

    class _Spec:
        label = "Explain ArtefactStore"

    class _Artifacts:
        draft_answer = "Old draft."
        prepared_briefing = "src/vaner/store/artefacts.py"

    class _Prediction:
        spec = _Spec()
        artifacts = _Artifacts()

    draft, refs = await drafter(_Prediction(), contract)  # type: ignore[arg-type]
    assert "ArtefactStore" in draft
    assert refs == ["store", "tests"]
    assert seen_prompts
    assert "Keep the exact same" in seen_prompts[0]
    assert "do not broaden, replace, or reinterpret" in seen_prompts[0]
    assert "Preserve these concrete target terms: artifactstore" in seen_prompts[0]
