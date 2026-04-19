# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import tiktoken


def count_tokens(text: str, model: str = "") -> int:
    """Count tokens in *text* using a tiktoken encoder.

    When *model* is empty or unknown, falls back to ``cl100k_base`` which is a
    good approximation for most current models (GPT-4, Claude, Llama families).
    Pass a specific model name only if exact token counts for that model matter.
    """
    if model:
        try:
            enc = tiktoken.encoding_for_model(model)
            return len(enc.encode(text))
        except KeyError:
            pass
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def enforce_budget(text_chunks: list[str], max_tokens: int, model: str = "") -> list[str]:
    kept: list[str] = []
    used = 0
    for chunk in text_chunks:
        chunk_tokens = count_tokens(chunk, model=model)
        if used + chunk_tokens > max_tokens:
            break
        kept.append(chunk)
        used += chunk_tokens
    return kept
