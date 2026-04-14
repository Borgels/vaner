# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.models.artefact import Artefact
from vaner.policy.budget import count_tokens


def compress_context(
    artefacts: list[Artefact],
    max_tokens: int,
) -> tuple[str, dict[str, int], int, set[str]]:
    token_map: dict[str, int] = {}
    kept_chunks: list[str] = []
    kept_keys: set[str] = set()
    used = 0

    for artefact in artefacts:
        chunk = f"### {artefact.source_path}\n{artefact.content}\n"
        chunk_tokens = count_tokens(chunk)
        token_map[artefact.key] = chunk_tokens
        if used + chunk_tokens > max_tokens:
            continue
        kept_chunks.append(chunk)
        kept_keys.add(artefact.key)
        used += chunk_tokens

    return "\n".join(kept_chunks), token_map, used, kept_keys
