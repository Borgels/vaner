# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_DENY_NAME_RE = re.compile(
    r"(^|[_\-.])("
    r"auth|oauth|token|login|logout|"
    r"precheck|place|modify|cancel|create|update|delete|submit|execute|write"
    r")([_\-.]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    name: str
    annotations: dict[str, Any] = field(default_factory=dict)
    provider_risk: str | None = None


@dataclass(frozen=True, slots=True)
class ToolSafetyDecision:
    allowed: bool
    reason: str
    tool_name: str


class ReadOnlyToolPolicy:
    """Provider-neutral guard for proactive MCP consumer calls.

    The policy is intentionally strict. A tool must be explicitly allowed and
    must be positively classified as read-only by annotations or provider risk
    metadata. Name patterns are a final deny layer, not the primary signal.
    """

    def __init__(
        self,
        *,
        allowed_tools: set[str] | list[str] | tuple[str, ...],
        tool_risks: dict[str, str] | None = None,
    ) -> None:
        self.allowed_tools = frozenset(str(tool) for tool in allowed_tools)
        self.tool_risks = {str(name): str(risk).strip().lower() for name, risk in (tool_risks or {}).items()}

    def validate(self, descriptor: McpToolDescriptor) -> ToolSafetyDecision:
        name = descriptor.name
        if name not in self.allowed_tools:
            return ToolSafetyDecision(False, "tool_not_in_allowlist", name)
        if _DENY_NAME_RE.search(name):
            return ToolSafetyDecision(False, "tool_name_matches_deny_pattern", name)
        risk = (descriptor.provider_risk or self.tool_risks.get(name) or "").strip().lower()
        if risk and risk != "read":
            return ToolSafetyDecision(False, f"provider_risk_not_read:{risk}", name)
        annotations = descriptor.annotations or {}
        read_hint = annotations.get("readOnlyHint")
        destructive_hint = annotations.get("destructiveHint")
        open_world_hint = annotations.get("openWorldHint")
        if destructive_hint is True:
            return ToolSafetyDecision(False, "destructive_hint_true", name)
        if open_world_hint is True and read_hint is not True:
            return ToolSafetyDecision(False, "open_world_without_readonly_hint", name)
        if read_hint is not True and risk != "read":
            return ToolSafetyDecision(False, "missing_positive_readonly_classification", name)
        return ToolSafetyDecision(True, "read_only_allowlisted", name)

    def validate_all(self, descriptors: list[McpToolDescriptor]) -> list[ToolSafetyDecision]:
        return [self.validate(descriptor) for descriptor in descriptors]
