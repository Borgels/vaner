# SPDX-License-Identifier: Apache-2.0
"""Tests for :mod:`vaner.router.translate.detect_format`.

These guard against the CodeQL ``py/incomplete-url-substring-sanitization``
class of bugs where a host is inferred from a substring match on the full URL
instead of the parsed hostname.
"""

from __future__ import annotations

import pytest

from vaner.router.translate import detect_format


@pytest.mark.parametrize(
    "url",
    [
        "https://api.anthropic.com/v1/messages",
        "https://API.Anthropic.Com/v1/messages",
        "https://subdomain.anthropic.com/v1/messages",
    ],
)
def test_detect_format_anthropic_hostnames(url: str) -> None:
    assert detect_format(url) == "anthropic"


@pytest.mark.parametrize(
    "url",
    [
        "https://generativelanguage.googleapis.com/v1beta",
        "https://aiplatform.googleapis.com/v1",
        "https://us-central1-aiplatform.googleapis.com/v1",
    ],
)
def test_detect_format_google_hostnames(url: str) -> None:
    assert detect_format(url) == "google"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://localhost:11434/v1",
        "http://127.0.0.1:8080",
        "https://vllm.internal:8000/v1",
    ],
)
def test_detect_format_defaults_to_openai(url: str) -> None:
    assert detect_format(url) == "openai"


@pytest.mark.parametrize(
    "url",
    [
        # Substring attacks: path or userinfo containing anthropic.com / googleapis.com
        "https://evil.example.com/anthropic.com",
        "https://anthropic.com.evil.test/v1",
        "https://user:pass@evil.example.com/anthropic.com/v1",
        "https://evil.example/?redirect=https://api.anthropic.com",
        "https://anthropic-com.evil/v1",
        "https://googleapis.com.evil.test/v1",
        "https://evil.example/generativelanguage.googleapis.com",
    ],
)
def test_detect_format_resists_substring_confusion(url: str) -> None:
    """Confusable URLs must fall back to the ``openai`` default."""

    assert detect_format(url) == "openai"


@pytest.mark.parametrize("url", ["", "not a url", "://", "ftp://"])
def test_detect_format_handles_malformed_inputs(url: str) -> None:
    assert detect_format(url) == "openai"
