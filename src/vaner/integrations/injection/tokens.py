# SPDX-License-Identifier: Apache-2.0
"""Token counting + budget-aware truncation for injected context blocks.

Reuses the tokenizer-aware counter from :mod:`vaner.intent.briefing` when a
real tokenizer is available. Otherwise falls back to the four-char heuristic
the briefing assembler uses for its own accounting.
"""

from __future__ import annotations

from collections.abc import Callable

TokenCounter = Callable[[str], int]


def _default_counter(text: str) -> int:
    # Same heuristic as BriefingAssembler when no tokenizer is injected.
    return max(0, len(text) // 4)


def count_tokens(text: str, *, tokenizer: TokenCounter | None = None) -> int:
    fn = tokenizer or _default_counter
    return fn(text)


def truncate_to_budget(
    text: str,
    *,
    budget_tokens: int,
    tokenizer: TokenCounter | None = None,
    marker: str = "…",
) -> str:
    """Trim *text* to stay within *budget_tokens*.

    Returns the original text when already under budget. Otherwise trims from
    the end and appends ``marker`` so downstream readers can see the cut.
    Never returns content over budget.
    """
    if budget_tokens <= 0:
        return ""
    if count_tokens(text, tokenizer=tokenizer) <= budget_tokens:
        return text
    # Binary-search over character prefixes. This is O(log n * tokenizer) but
    # keeps us correct for both the char-heuristic and real BPE tokenizers.
    lo, hi = 0, len(text)
    best = ""
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid] + marker
        if count_tokens(candidate, tokenizer=tokenizer) <= budget_tokens:
            best = candidate
            lo = mid
        else:
            hi = mid - 1
    return best or marker
