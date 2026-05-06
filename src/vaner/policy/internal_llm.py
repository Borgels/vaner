# SPDX-License-Identifier: Apache-2.0
"""Internal prompt policy for Vaner's background LLM pipeline.

These blocks are for machine-consumed intermediate calls, not user-facing
assistant personas. Keep them short so local models spend context on evidence
and the requested output contract.
"""

from __future__ import annotations

CORE_POLICY = """Vaner internal LLM policy:
- Preserve the requested output contract over style.
- Prefer exact supplied evidence over plausible inference.
- Preserve implementation anchors exactly: paths, symbols, constants, config keys, routes, database fields, errors, limits, and conditions.
- Use null, empty arrays, low confidence, or short evidence notes when evidence is missing; do not invent details.
- Do not expose hidden reasoning unless the output schema explicitly requires it."""

JSON_CONTRACT_POLICY = """JSON contract:
- Return valid JSON only, with no markdown fences, preamble, commentary, or trailing prose.
- Use only the requested keys and valid JSON values.
- Keep rationale/reason fields to short evidence notes, not chain-of-thought."""

EVIDENCE_SUMMARY_POLICY = """Evidence summary policy:
- Summaries must be grounded only in supplied files, diffs, code, or context.
- Do not let predictions or likely intent become factual behavior.
- Prefer exact implementation references over generic descriptions."""

PREDICTION_POLICY = """Prediction policy:
- Speculate only because this task asks for prediction.
- Tie predictions to observed signals; label uncertainty through confidence, rationale, evidence, or provenance.
- Keep predictive claims separate from factual summaries."""

DRAFT_POLICY = """Draft policy:
- Drafts remain evidence-bound and useful to a downstream AI client.
- Use tentative wording when source evidence is incomplete.
- Avoid overstating what Vaner knows."""


def internal_llm_policy(*blocks: str) -> str:
    """Compose the core policy with task-specific overlays."""

    selected = [CORE_POLICY, *[block for block in blocks if block.strip()]]
    return "\n".join(selected)
