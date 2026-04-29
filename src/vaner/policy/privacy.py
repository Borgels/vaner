# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import fnmatch
import re
import warnings
from pathlib import Path
from typing import Any


def path_is_allowed(path: str, excluded_patterns: list[str]) -> bool:
    filename = Path(path).name
    for pattern in excluded_patterns:
        if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(path, pattern):
            return False
    return True


def redact_text(text: str, patterns: list[str]) -> str:
    redacted = text
    for pattern in patterns:
        try:
            redacted = re.sub(pattern, "[REDACTED]", redacted, flags=re.IGNORECASE)
        except re.error:
            warnings.warn(f"Skipping invalid redact pattern: {pattern}", RuntimeWarning, stacklevel=2)
    return redacted


_ABSOLUTE_LOCAL_PATH_RE = re.compile(
    r"(?<![\w@])"
    r"(?:"
    r"/(?:home|Users|private|tmp|var|opt|srv|mnt|media)/[^\s\"'<>),;]+"
    r"|[A-Za-z]:\\[^\s\"'<>),;]+"
    r")"
)


def contains_absolute_local_path(value: str) -> bool:
    """Return True when *value* appears to contain a local absolute path."""
    return bool(_ABSOLUTE_LOCAL_PATH_RE.search(value))


def redact_absolute_local_paths(value: str) -> str:
    """Remove local absolute paths from text before telemetry/report storage."""
    return _ABSOLUTE_LOCAL_PATH_RE.sub("[LOCAL_PATH]", value)


def sanitize_no_absolute_paths(value: Any) -> Any:
    """Recursively redact absolute local paths from JSON-like values.

    This is intentionally conservative and string-based: telemetry and
    benchmark payloads should store relative repo paths or stable ids only.
    """
    if isinstance(value, str):
        return redact_absolute_local_paths(value)
    if isinstance(value, list):
        return [sanitize_no_absolute_paths(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_no_absolute_paths(item) for item in value)
    if isinstance(value, dict):
        return {str(sanitize_no_absolute_paths(key)): sanitize_no_absolute_paths(item) for key, item in value.items()}
    return value
