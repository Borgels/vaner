# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.policy.privacy import (
    contains_absolute_local_path,
    path_is_allowed,
    redact_absolute_local_paths,
    redact_text,
    sanitize_no_absolute_paths,
)


def test_path_is_allowed_with_exclusions():
    assert path_is_allowed("src/app.py", ["*.env"]) is True
    assert path_is_allowed("prod.env", ["*.env"]) is False


def test_redact_text_skips_invalid_regex():
    text = "api key is secret"
    with pytest.warns(RuntimeWarning):
        redacted = redact_text(text, [r"secret", r"(broken"])
    assert "[REDACTED]" in redacted


def test_absolute_local_paths_are_redacted_from_text():
    text = "Read /tmp/example/repo/src/app.py and src/ok.py"
    redacted = redact_absolute_local_paths(text)
    assert "/tmp/example" not in redacted
    assert "[LOCAL_PATH]" in redacted
    assert "src/ok.py" in redacted


def test_absolute_local_paths_are_sanitized_recursively():
    payload = {
        "path": "/tmp/example/repo/src/app.py",
        "nested": ["keep/relative.py", "C:\\Users\\name\\repo\\app.py"],
    }
    sanitized = sanitize_no_absolute_paths(payload)
    assert sanitized["path"] == "[LOCAL_PATH]"
    assert sanitized["nested"][0] == "keep/relative.py"
    assert sanitized["nested"][1] == "[LOCAL_PATH]"
    assert not contains_absolute_local_path(str(sanitized))
