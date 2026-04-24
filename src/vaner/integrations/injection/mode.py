# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from enum import StrEnum


class ContextInjectionMode(StrEnum):
    """How aggressively Vaner should inject prepared material into LLM context.

    Defaults per client capability tier (see
    :mod:`vaner.integrations.capability`):

    * Tier 1 (MCP-only) → :attr:`NONE`
    * Tier 2 (+ prompt guidance) → :attr:`DIGEST_ONLY`
    * Tier 3 (+ context mediation) → :attr:`POLICY_HYBRID`
    * Tier 4 (+ MCP Apps UI) → :attr:`CLIENT_CONTROLLED`
    """

    NONE = "none"
    """Never inject. The client handles it or does without."""

    DIGEST_ONLY = "digest_only"
    """Inject a short prediction digest when relevant; never the full package."""

    ADOPTED_PACKAGE_ONLY = "adopted_package_only"
    """Inject the full adopted package when a fresh one is available. Never emit a digest."""

    TOP_MATCH_AUTO_INCLUDE = "top_match_auto_include"
    """Auto-include the top prediction as a full adopted package when ready+fresh+confident."""

    POLICY_HYBRID = "policy_hybrid"
    """Emit digest for maturing predictions; adopted-package when the user already adopted."""

    CLIENT_CONTROLLED = "client_controlled"
    """Client assembles context (e.g. via MCP Apps UI + direct tool calls). Server stays out of the way."""
