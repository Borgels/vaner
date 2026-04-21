# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Awaitable, Callable


def sentence_transformer_embed(
    *,
    model: str = "all-MiniLM-L6-v2",
    device: str = "cpu",
) -> Callable[[list[str]], Awaitable[list[list[float]]]]:
    from sentence_transformers import SentenceTransformer

    encoder = None

    async def _call(texts: list[str]) -> list[list[float]]:
        nonlocal encoder
        if encoder is None:
            encoder = SentenceTransformer(model, device=device)
        vectors = encoder.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]

    return _call
