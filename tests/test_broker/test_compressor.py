# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from vaner.broker.compressor import compress_context
from vaner.models.artefact import Artefact, ArtefactKind


def _artefact(key: str, source: str, content: str) -> Artefact:
    ts = time.time()
    return Artefact(
        key=key,
        kind=ArtefactKind.FILE_SUMMARY,
        source_path=source,
        source_mtime=ts,
        generated_at=ts,
        model="test",
        content=content,
    )


def test_compressor_empty_input():
    context, token_map, used, kept = compress_context([], max_tokens=100)
    assert context == ""
    assert token_map == {}
    assert used == 0
    assert kept == set()


def test_compressor_single_chunk_over_budget():
    artefacts = [_artefact("a", "a.py", "word " * 400)]
    context, token_map, used, kept = compress_context(artefacts, max_tokens=10)
    assert context == ""
    assert token_map["a"] > 10
    assert used == 0
    assert kept == set()


def test_compressor_counts_tokens_once():
    artefacts = [_artefact("a", "a.py", "alpha " * 20), _artefact("b", "b.py", "beta " * 20)]
    _, token_map, used, kept = compress_context(artefacts, max_tokens=200)
    assert used == sum(token_map[key] for key in kept)


def test_compressor_prefers_high_score_when_order_unsorted():
    artefacts = [
        _artefact("low", "low.py", "tiny chunk"),
        _artefact("high", "high.py", "tiny chunk"),
    ]
    context, _, _, kept = compress_context(
        artefacts,
        max_tokens=100,
        score_by_key={"high": 10.0, "low": 1.0},
    )
    assert kept == {"high", "low"}
    assert context.index("high.py") < context.index("low.py")
