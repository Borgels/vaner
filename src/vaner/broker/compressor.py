# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.models.artefact import Artefact
from vaner.policy.budget import count_tokens


def compress_context(
    artefacts: list[Artefact],
    max_tokens: int,
    score_by_key: dict[str, float] | None = None,
) -> tuple[str, dict[str, int], int, set[str]]:
    token_map: dict[str, int] = {}
    chunk_map: dict[str, str] = {}
    ordered_keys: list[str] = []
    kept_keys: set[str] = set()
    used = 0

    for artefact in artefacts:
        chunk = f"### {artefact.source_path}\n{artefact.content}\n"
        chunk_tokens = count_tokens(chunk)
        token_map[artefact.key] = chunk_tokens
        chunk_map[artefact.key] = chunk
        ordered_keys.append(artefact.key)

    if score_by_key is not None:
        ordered_keys.sort(key=lambda key: score_by_key.get(key, 0.0), reverse=True)

    # Greedy knapsack approximation: pack highest-score artefacts first while
    # still trying lower-ranked/smaller chunks that fit remaining budget.
    for key in ordered_keys:
        chunk_tokens = token_map[key]
        if used + chunk_tokens > max_tokens:
            continue
        kept_keys.add(key)
        used += chunk_tokens

    kept_chunks = [chunk_map[key] for key in ordered_keys if key in kept_keys]
    return "\n".join(kept_chunks), token_map, used, kept_keys
