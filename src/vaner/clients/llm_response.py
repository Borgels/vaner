# SPDX-License-Identifier: Apache-2.0
"""LLMResponse — the content contract for reasoning-capable LLM backends.

Phase 4 / Phase B of 0.8.0 reframes the content contract so reasoning-model
preambles (Qwen3's "Thinking Process:", Claude's ``<thinking>``, DeepSeek's
``<think>``, gpt-5's hidden thinking) are captured or stripped without
forcing the caller to disable thinking at the provider level.

Strategy order (hierarchical, not mutually exclusive):
  1. Structured output — if the provider supports ``response_format``, that
     guarantees the content shape. Thinking stays in whatever channel the
     provider uses (often omitted from the structured reply).
  2. Tolerant post-processing — for providers that don't support structured
     output, strip known thinking-block patterns before the JSON extraction.
  3. Explicit separation — the adapter returns ``(thinking, content, raw)``
     so observability never has to fight the content contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Separated view of an LLM reply.

    - ``thinking``: reasoning preamble captured for observability (may be "").
    - ``content``: the post-stripped payload ready for downstream parsing
      (typically JSON). Never ``None`` — empty string if nothing remained.
    - ``raw``: the unmodified response text for debugging and auditing.
    """

    thinking: str
    content: str
    raw: str

    def __str__(self) -> str:
        # Legacy callers that treat an LLM response as a bare string see
        # only the content payload — not the thinking preamble.
        return self.content


# ---------------------------------------------------------------------------
# Thinking-block strippers
# ---------------------------------------------------------------------------

# Priority-ordered list of (pattern, flags, label). First match wins so a
# response carrying multiple markers (rare) is still stripped predictably.
_STRIPPERS: list[tuple[re.Pattern[str], str]] = [
    # Claude / Anthropic style. Case-insensitive, spans newlines.
    (re.compile(r"<thinking>(.*?)</thinking>", re.IGNORECASE | re.DOTALL), "claude_thinking"),
    # DeepSeek style: <think>…</think>
    (re.compile(r"<think>(.*?)</think>", re.IGNORECASE | re.DOTALL), "deepseek_think"),
    # Qwen3 "Thinking Process:" preamble — spans to the first '{' (JSON onset)
    # or to the first blank line, whichever comes first.
    (
        re.compile(
            r"^\s*(?:thinking process|thought process|reasoning)\s*[:\-]?\s*(.*?)(?=^\s*\{|^\s*\[|\n\s*\n)",
            re.IGNORECASE | re.DOTALL | re.MULTILINE,
        ),
        "qwen3_thinking_process",
    ),
    # Fenced reasoning block (some fine-tunes use markdown fences)
    (
        re.compile(r"```(?:reasoning|thinking|think)\s*\n(.*?)\n```", re.IGNORECASE | re.DOTALL),
        "fenced_reasoning",
    ),
]


def split_thinking_and_content(raw: str) -> LLMResponse:
    """Extract any known thinking preamble. If none matches, content is raw."""
    if not raw:
        return LLMResponse(thinking="", content="", raw=raw or "")

    for pattern, _label in _STRIPPERS:
        match = pattern.search(raw)
        if match is None:
            continue
        thinking = match.group(1).strip()
        # Content = raw with the matched thinking block removed.
        content = (raw[: match.start()] + raw[match.end() :]).strip()
        return LLMResponse(thinking=thinking, content=content, raw=raw)

    return LLMResponse(thinking="", content=raw.strip(), raw=raw)


# ---------------------------------------------------------------------------
# Simple token approximation (used when no provider-side count is available)
# ---------------------------------------------------------------------------


def approx_tokens(text: str) -> int:
    """Rough character-based token estimate.

    We use ~4 chars per token as a conservative low-overhead fallback when
    neither the provider nor a tokenizer library is available. Callers that
    need precision should prefer provider-reported counts.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)
