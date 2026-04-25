# SPDX-License-Identifier: Apache-2.0
"""MCP Apps manifest for Vaner's active-predictions UI resource.

The resource URI + CSP metadata match the `@modelcontextprotocol/ext-apps`
convention: `ui://<server>/<view>.html` with MIME `text/html;profile=mcp-app`
and a `ui` meta entry that scopes network access via `csp.resourceDomains`.

0.8.5 WS13: the `@modelcontextprotocol/ext-apps@0.4.0` SDK is now inlined
verbatim into `active_predictions.html` (vendored from the npm tarball;
SHA-256 in the file's header banner). The iframe needs **zero external
network** at runtime — `csp.resourceDomains` is empty.
"""

from __future__ import annotations

from typing import Any, Final

ACTIVE_PREDICTIONS_URI: Final[str] = "ui://vaner/active-predictions"
ACTIVE_PREDICTIONS_MIME: Final[str] = "text/html;profile=mcp-app"
ACTIVE_PREDICTIONS_NAME: Final[str] = "Vaner Active Predictions"
ACTIVE_PREDICTIONS_TITLE: Final[str] = "Vaner — Active Predictions"
ACTIVE_PREDICTIONS_DESCRIPTION: Final[str] = (
    "Interactive dashboard for Vaner's prepared next predictions. "
    "Ranks adoptable-first, shows readiness + ETA, click Adopt to "
    "convert a prediction into the next Resolution."
)

# Which domains the sandboxed iframe may reach. With the ext-apps SDK
# inlined into active_predictions.html (0.8.5 WS13), the iframe needs
# zero external network — every state-changing action goes through the
# host's postMessage bridge.
CSP_RESOURCE_DOMAINS: Final[tuple[str, ...]] = ()


def resource_meta() -> dict[str, Any]:
    """The `_meta` dict attached to the MCP Resource on registration.

    The `ui` key follows the ext-apps convention (see the `qr-server`
    reference in `github.com/modelcontextprotocol/ext-apps`).
    """
    return {
        "ui": {
            "csp": {"resourceDomains": list(CSP_RESOURCE_DOMAINS)},
        },
    }


def tool_meta() -> dict[str, Any]:
    """The `_meta` dict attached to the dashboard tool so UI-capable hosts
    know which resource to render when the tool is called."""
    return {
        "ui": {"resourceUri": ACTIVE_PREDICTIONS_URI},
        "ui/resourceUri": ACTIVE_PREDICTIONS_URI,
    }
