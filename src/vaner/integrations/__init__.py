# SPDX-License-Identifier: Apache-2.0
"""Integration layer — guidance assets, capability tiers, context injection.

See the 0.8.5 plan for the full integration architecture. This package is the
single home for everything that bridges Vaner's prediction registry to the
various surfaces (MCP tools, HTTP, MCP Apps UI, desktop handoff).
"""

from vaner.integrations.guidance import (
    GuidanceDoc,
    GuidanceVariant,
    available_variants,
    current_version,
    load_guidance,
)

__all__ = [
    "GuidanceDoc",
    "GuidanceVariant",
    "available_variants",
    "current_version",
    "load_guidance",
]
