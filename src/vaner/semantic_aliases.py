# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Collection


def engineering_semantic_aliases(text: str, tokens: Collection[str], *, stopwords: Collection[str] = ()) -> set[str]:
    """Expand common engineering paraphrases without model calls.

    These aliases are intentionally broad product vocabulary, not benchmark
    fixtures. They help Vaner keep exact evidence in view when users describe
    a concept in plain language while code/docs use implementation terms.
    """

    lowered = text.lower().replace("_", " ").replace("-", " ")
    normalized_tokens = {token.lower() for token in tokens}
    token_text = " ".join(sorted(normalized_tokens))
    haystack = f"{lowered} {token_text}"
    aliases: set[str] = set()

    def has_any(*phrases: str) -> bool:
        return any(phrase in haystack for phrase in phrases)

    if has_any(
        "dry run",
        "smoke check",
        "smoke policy",
        "full traffic",
        "candidate release",
        "staged promote",
        "staged rollout",
    ):
        aliases.update({"rehearse", "rehearsal", "replay", "canary", "escrow", "promote", "promotion", "traffic"})
    if has_any("rehearse", "rehearsal", "replay", "traffic escrow", "promote", "promotion"):
        aliases.update({"dry", "run", "smoke", "canary", "candidate", "release", "rollout", "traffic", "gate", "gating"})

    if has_any("low bit", "low precision", "numeric mode", "mixed precision", "quantization", "quantized"):
        aliases.update(
            {"precision", "quantization", "quant", "kernel", "stability", "threshold", "annealing", "int4", "int8", "fp16", "fp32"}
        )
    if has_any("precision annealing", "kernel stability", "stability threshold", "int4", "int8", "fp16", "fp32"):
        aliases.update({"low", "bit", "numeric", "mode", "quantization", "safest", "pass", "rate"})

    if has_any("vectorization", "vectorize", "embedding", "embeddings", "embed batch", "batch embeds"):
        aliases.update({"embedding", "embeddings", "embed", "vector", "vectorize", "vectorization"})

    if has_any("western europe", "europe", "european", "eu west", "eu central"):
        aliases.update({"eu", "euwest", "eucentral", "europe", "european", "emea"})
    if has_any("southeast asia", "south east asia", "apac", "ap southeast"):
        aliases.update({"apac", "asia", "southeast", "apsoutheast"})
    if has_any("egress", "residency", "cross region", "edge fallback", "routing anomaly", "routed"):
        aliases.update({"route", "routing", "egress", "residency", "fallback", "failover", "edge", "control", "plane"})

    if has_any("makes things up", "made things up", "hallucination", "hallucinate", "joke", "meme"):
        aliases.update({"hallucination", "hallucinate", "meme", "memes", "satire", "tagging", "joke", "jokes"})

    stopword_set = {word.lower() for word in stopwords}
    return {alias for alias in aliases if len(alias) >= 3 and alias not in stopword_set}
