#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Atheris fuzz target for :func:`vaner.router.translate.detect_format`.

Run locally::

    pip install atheris
    python tests/fuzz/fuzz_translate.py -atheris_runs=100000

Invariants asserted:

- ``detect_format`` always returns one of the three known labels.
- It never raises on any bytes/str input.
- It never classifies a URL as ``"anthropic"`` or ``"google"`` unless the
  parsed hostname actually matches those providers (guards against the
  CodeQL ``py/incomplete-url-substring-sanitization`` class).
"""

from __future__ import annotations

import sys
from urllib.parse import urlparse

import atheris

# NOTE: Do not call `atheris.instrument_imports()` around vaner imports.
# Atheris instruments every transitively imported module; vaner pulls in
# fastapi, pydantic, numpy, lightgbm, etc., which segfaults the instrumenter.
# The target (`detect_format`) is small and pure, so Atheris's fuzzer driver
# alone (mutation over bytes) is sufficient to hit every branch.
from vaner.router.translate import detect_format

_VALID = frozenset({"openai", "anthropic", "google"})


def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    url = fdp.ConsumeUnicodeNoSurrogates(512)

    try:
        result = detect_format(url)
    except Exception as exc:  # pragma: no cover - must never raise
        raise AssertionError(f"detect_format raised on input {url!r}: {exc!r}") from exc

    assert result in _VALID, f"unexpected label {result!r} for {url!r}"

    if result in ("anthropic", "google"):
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        if result == "anthropic":
            assert host == "api.anthropic.com" or host.endswith(
                ".anthropic.com"
            ), f"false-positive anthropic for host={host!r} url={url!r}"
        else:
            assert host.endswith(".googleapis.com") or host in {
                "generativelanguage.googleapis.com",
                "aiplatform.googleapis.com",
            }, f"false-positive google for host={host!r} url={url!r}"


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
