# SPDX-License-Identifier: Apache-2.0
"""Integration layer — guidance assets, capability tiers, context injection.

See the 0.8.5 plan for the full integration architecture. This package is the
single home for everything that bridges Vaner's prediction registry to the
various surfaces (MCP tools, HTTP, MCP Apps UI, desktop handoff).
"""

from vaner.integrations.capability import (
    ClientCapabilityTier,
    TierDetection,
    current_detection,
    current_tier,
    detect_tier,
    record_tier,
)
from vaner.integrations.guidance import (
    GuidanceDoc,
    GuidanceVariant,
    available_variants,
    current_version,
    load_guidance,
)
from vaner.integrations.injection import (
    AdoptedPackagePayload,
    ContextInjectionMode,
    DigestEntry,
    InjectionDecision,
    InjectionInputs,
    build_adopted_package,
    build_digest,
    should_inject,
)

__all__ = [
    "AdoptedPackagePayload",
    "ClientCapabilityTier",
    "ContextInjectionMode",
    "DigestEntry",
    "GuidanceDoc",
    "GuidanceVariant",
    "InjectionDecision",
    "InjectionInputs",
    "TierDetection",
    "available_variants",
    "build_adopted_package",
    "build_digest",
    "current_detection",
    "current_tier",
    "current_version",
    "detect_tier",
    "load_guidance",
    "record_tier",
    "should_inject",
]
