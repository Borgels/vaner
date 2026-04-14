# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.policy.privacy import path_is_allowed, redact_text


def test_path_is_allowed_with_exclusions():
    assert path_is_allowed("src/app.py", ["*.env"]) is True
    assert path_is_allowed("prod.env", ["*.env"]) is False


def test_redact_text_skips_invalid_regex():
    text = "api key is secret"
    with pytest.warns(RuntimeWarning):
        redacted = redact_text(text, [r"secret", r"(broken"])
    assert "[REDACTED]" in redacted
