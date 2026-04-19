# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import fnmatch
import re
import warnings
from pathlib import Path


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
