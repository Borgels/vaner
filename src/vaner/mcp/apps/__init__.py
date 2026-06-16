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


_SDK_START_SENTINEL = "// === @modelcontextprotocol/ext-apps@0.4.0"
_SDK_END_SENTINEL = ";const App = gc;"


def _extract_ext_apps_sdk(html: str) -> str:
    """Pull the vendored ext-apps SDK + ``const App = gc`` rebind from a bundle.

    ``active_predictions.html`` is the single source of truth for the
    vendored SDK; other bundles (currently ``prepared_work.html``) inline
    the same block via a ``__EXT_APPS_SDK__`` placeholder substitution at
    import time. Keeping one copy avoids drift on re-vendor and means we
    only ship the ~300KB SDK twice if a host actually loads both bundles.
    """
    start = html.find(_SDK_START_SENTINEL)
    if start < 0:
        raise RuntimeError("ext-apps SDK start sentinel missing from active_predictions.html")
    end = html.find(_SDK_END_SENTINEL, start)
    if end < 0:
        raise RuntimeError("ext-apps SDK end sentinel missing from active_predictions.html")
    end += len(_SDK_END_SENTINEL)
    return html[start:end]


EXT_APPS_SDK_JS: str = _extract_ext_apps_sdk(ACTIVE_PREDICTIONS_HTML)
"""Vendored ``@modelcontextprotocol/ext-apps`` SDK + ``const App = gc`` rebind.

Sliced from ``active_predictions.html`` between the vendor banner and the
trailing ``const App = gc`` rebinding. Bundles that need the SDK include
``__EXT_APPS_SDK__`` as a placeholder; :func:`_inline_sdk` replaces it at
import time.
"""


def _inline_sdk(template: str) -> str:
    return template.replace("__EXT_APPS_SDK__", EXT_APPS_SDK_JS)


PREPARED_WORK_HTML: str = _inline_sdk(_PREPARED_WORK_PATH.read_text(encoding="utf-8"))
"""Full Prepared Work HTML bundle as a string (SDK inlined at import time)."""

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
    "EXT_APPS_SDK_JS",
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
