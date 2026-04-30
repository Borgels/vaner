# SPDX-License-Identifier: Apache-2.0
"""MCP Apps bundle loader.

Reads the static `active_predictions.html` bundle at import time so the
MCP server can serve it directly without touching disk on every request.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from vaner.mcp.apps.manifest import (
    ACTIVE_PREDICTIONS_DESCRIPTION,
    ACTIVE_PREDICTIONS_MIME,
    ACTIVE_PREDICTIONS_NAME,
    ACTIVE_PREDICTIONS_TITLE,
    ACTIVE_PREDICTIONS_URI,
    CSP_RESOURCE_DOMAINS,
    PREPARED_WORK_DESCRIPTION,
    PREPARED_WORK_MIME,
    PREPARED_WORK_NAME,
    PREPARED_WORK_TITLE,
    PREPARED_WORK_URI,
    prepared_work_tool_meta,
    resource_meta,
    tool_meta,
)

_BUNDLE_DIR = Path(__file__).parent
_ACTIVE_PREDICTIONS_PATH = _BUNDLE_DIR / "active_predictions.html"
_PREPARED_WORK_PATH = _BUNDLE_DIR / "prepared_work.html"

ACTIVE_PREDICTIONS_HTML: str = _ACTIVE_PREDICTIONS_PATH.read_text(encoding="utf-8")
"""Full HTML bundle as a string. Consumers serve this verbatim."""
PREPARED_WORK_HTML: str = _PREPARED_WORK_PATH.read_text(encoding="utf-8")
"""Full Prepared Work HTML bundle as a string."""

ACTIVE_PREDICTIONS_SHA256: str = hashlib.sha256(ACTIVE_PREDICTIONS_HTML.encode("utf-8")).hexdigest()
"""Stable content hash — useful for cache validation + CSP pinning."""
PREPARED_WORK_SHA256: str = hashlib.sha256(PREPARED_WORK_HTML.encode("utf-8")).hexdigest()


__all__ = [
    "ACTIVE_PREDICTIONS_DESCRIPTION",
    "ACTIVE_PREDICTIONS_HTML",
    "ACTIVE_PREDICTIONS_MIME",
    "ACTIVE_PREDICTIONS_NAME",
    "ACTIVE_PREDICTIONS_SHA256",
    "ACTIVE_PREDICTIONS_TITLE",
    "ACTIVE_PREDICTIONS_URI",
    "CSP_RESOURCE_DOMAINS",
    "PREPARED_WORK_DESCRIPTION",
    "PREPARED_WORK_HTML",
    "PREPARED_WORK_MIME",
    "PREPARED_WORK_NAME",
    "PREPARED_WORK_SHA256",
    "PREPARED_WORK_TITLE",
    "PREPARED_WORK_URI",
    "prepared_work_tool_meta",
    "resource_meta",
    "tool_meta",
]
