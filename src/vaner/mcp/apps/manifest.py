# SPDX-License-Identifier: Apache-2.0
"""MCP Apps manifest for Vaner's active-predictions UI resource.

The resource URI + CSP metadata match the `@modelcontextprotocol/ext-apps`
convention: `ui://<server>/<view>.html` with MIME `text/html;profile=mcp-app`
and a `ui` meta entry that scopes network access via `csp.resourceDomains`.

We allow only `unpkg.com` for the pinned ext-apps SDK import. Every other
network egress is blocked by the host CSP applied to the iframe.
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

# Which domains the sandboxed iframe may reach. Only the pinned ext-apps
# SDK loader. Everything else — including the local daemon — is reached
# through the host's postMessage bridge, never directly.
CSP_RESOURCE_DOMAINS: Final[tuple[str, ...]] = ("https://unpkg.com",)


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
