# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import tiktoken


def count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def enforce_budget(text_chunks: list[str], max_tokens: int, model: str = "gpt-4o-mini") -> list[str]:
    kept: list[str] = []
    used = 0
    for chunk in text_chunks:
        chunk_tokens = count_tokens(chunk, model=model)
        if used + chunk_tokens > max_tokens:
            break
        kept.append(chunk)
        used += chunk_tokens
    return kept
