# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Sequence

EVIDENCE_BOUND_CONTEXT_HEADER = """Use the Vaner-prepared evidence below when it is relevant.

Evidence rules:
- Treat the evidence as the source of truth for implementation details.
- Do not invent files, symbols, constants, thresholds, line numbers, or behavior that are not present in the evidence.
- If the evidence is incomplete, state what is supported and what remains uncertain instead of filling gaps from prior knowledge.
- When you make an inference beyond a direct excerpt, label it as an inference.
"""


def build_evidence_bound_context_prompt(
    context: str,
    *,
    facets: Sequence[str] = (),
    max_chars: int | None = None,
) -> str:
    """Build a system prompt that keeps answer generation anchored to evidence."""

    bounded_context = context if max_chars is None else context[:max(0, int(max_chars))]
    facet_lines = [facet.strip() for facet in facets if facet.strip()]
    facet_block = ""
    if facet_lines:
        bullets = "\n".join(f"- {facet}" for facet in facet_lines)
        facet_block = f"\nQuestion facets to cover when evidence supports them:\n{bullets}\n"
    return f"{EVIDENCE_BOUND_CONTEXT_HEADER}{facet_block}\nPrepared evidence:\n\n{bounded_context}"
