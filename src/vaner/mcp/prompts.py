# SPDX-License-Identifier: Apache-2.0

"""Slash-command-style MCP prompts for Vaner.

Hosts that surface ``prompts/list`` (Claude Code, Claude Desktop, VS Code Copilot,
Goose, …) render these as native slash commands. Each prompt expands into a
short user-role message that asks the AI to call the matching ``vaner.*`` tool
with the user's argument values.

The point of this module is to make Vaner reachable in one keystroke without
the model having to decide to call a tool. It is intentionally small: every
prompt maps 1:1 to an existing tool. New prompts must add real user-facing
value, not just wrap an internal call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.types import GetPromptResult, Prompt


@dataclass(frozen=True)
class _PromptArg:
    name: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class _PromptSpec:
    name: str
    title: str
    description: str
    arguments: tuple[_PromptArg, ...] = ()
    template: str = ""

    def render(self, arguments: dict[str, str] | None) -> str:
        args = arguments or {}
        rendered = self.template
        for arg in self.arguments:
            value = args.get(arg.name, "").strip()
            placeholder = "{" + arg.name + "}"
            if placeholder in rendered:
                rendered = rendered.replace(placeholder, value)
        return rendered.strip()


_SPECS: tuple[_PromptSpec, ...] = (
    _PromptSpec(
        name="vaner-resolve",
        title="Vaner: prepare context for a task",
        description=(
            "Ask Vaner to prepare a ranked, evidence-backed context package "
            "for the described task. Returns a Resolution (briefing + draft + "
            "evidence + resolution_id)."
        ),
        arguments=(
            _PromptArg(
                name="task",
                description="Short description of what the user is about to work on.",
                required=True,
            ),
        ),
        template=(
            "Call the `vaner.resolve` MCP tool with `query` set to: "
            "{task}\n\n"
            "Use the returned briefing, draft, and evidence to answer. Keep "
            "the `resolution_id` so we can record feedback when the task is "
            "done."
        ),
    ),
    _PromptSpec(
        name="vaner-suggest",
        title="Vaner: what are you predicting next?",
        description=(
            "Show what Vaner currently predicts the user is about to ask, "
            "with confidence and readiness."
        ),
        template=(
            "Call the `vaner.suggest` MCP tool and summarise the top "
            "predictions (intent, confidence, readiness) as a short list. "
            "Do not pick one for the user — wait for their direction."
        ),
    ),
    _PromptSpec(
        name="vaner-dashboard",
        title="Vaner: open the predictions dashboard",
        description=(
            "Open the active-predictions panel. On MCP-Apps-capable hosts "
            "this attaches an interactive UI; on terminal hosts it returns a "
            "ranked text summary."
        ),
        template=(
            "Call the `vaner.predictions.dashboard` MCP tool. If the host "
            "renders the attached `ui://vaner/active-predictions` resource, "
            "let the user interact with it. Otherwise present the "
            "fallback_text verbatim."
        ),
    ),
    _PromptSpec(
        name="vaner-prepared",
        title="Vaner: show prepared work",
        description=(
            "Open the prepared-work panel — a unified view of artefacts and "
            "ready predictions Vaner has staged for the user."
        ),
        template=(
            "Call the `vaner.prepared_work.dashboard` MCP tool. If the host "
            "renders the attached `ui://vaner/prepared-work` resource, let "
            "the user interact with it. Otherwise summarise the cards as a "
            "numbered list."
        ),
    ),
    _PromptSpec(
        name="vaner-adopt",
        title="Vaner: adopt a prediction",
        description=(
            "Adopt a specific prediction by id, marking it as the user's "
            "actual intent. Returns the prepared Resolution."
        ),
        arguments=(
            _PromptArg(
                name="id",
                description="The prediction id to adopt (from vaner.suggest or the dashboard).",
                required=True,
            ),
        ),
        template=(
            "Call the `vaner.predictions.adopt` MCP tool with `id` set to "
            "`{id}` and `source` set to `\"prompt\"`. Use the returned "
            "Resolution to answer."
        ),
    ),
    _PromptSpec(
        name="vaner-feedback",
        title="Vaner: record feedback for a resolution",
        description=(
            "Tell Vaner whether a resolution was useful. Closes the "
            "scenario-ranking learning loop."
        ),
        arguments=(
            _PromptArg(
                name="resolution_id",
                description="Resolution id returned by vaner.resolve or vaner.predictions.adopt.",
                required=True,
            ),
            _PromptArg(
                name="verdict",
                description="One of: useful | partial | wrong | irrelevant.",
                required=True,
            ),
            _PromptArg(
                name="correction",
                description="Optional one-line correction or note.",
                required=False,
            ),
        ),
        template=(
            "Call the `vaner.feedback` MCP tool with `resolution_id` "
            "`{resolution_id}` and `verdict` `{verdict}`. If a correction "
            "was given, pass it as `correction`: {correction}."
        ),
    ),
    _PromptSpec(
        name="vaner-status",
        title="Vaner: engine status",
        description="Report Vaner engine health, readiness, and memory quality.",
        template=(
            "Call the `vaner.status` MCP tool and present `state`, "
            "`readiness`, and any warning fields in a short bullet list."
        ),
    ),
)


def list_specs() -> tuple[_PromptSpec, ...]:
    """Return the configured prompt specs (test/inspection hook)."""

    return _SPECS


def build_prompts() -> list[Prompt]:
    """Construct ``mcp.types.Prompt`` instances for ``prompts/list``."""

    from mcp.types import Prompt, PromptArgument

    prompts: list[Prompt] = []
    for spec in _SPECS:
        prompt_args = [
            PromptArgument(
                name=arg.name,
                description=arg.description,
                required=arg.required,
            )
            for arg in spec.arguments
        ]
        prompts.append(
            Prompt(
                name=spec.name,
                title=spec.title,
                description=spec.description,
                arguments=prompt_args or None,
            )
        )
    return prompts


def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
    """Resolve a single prompt by name into a ``GetPromptResult``.

    Raises ``ValueError`` if ``name`` is unknown or a required argument is
    missing — the SDK turns that into a JSON-RPC error.
    """

    from mcp.types import GetPromptResult, PromptMessage, TextContent

    spec = next((s for s in _SPECS if s.name == name), None)
    if spec is None:
        raise ValueError(f"unknown vaner prompt: {name!r}")

    args = arguments or {}
    missing = [arg.name for arg in spec.arguments if arg.required and not (args.get(arg.name) or "").strip()]
    if missing:
        raise ValueError(
            f"missing required arguments for {spec.name!r}: {', '.join(missing)}"
        )

    rendered = spec.render(args)
    return GetPromptResult(
        description=spec.description,
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=rendered),
            )
        ],
    )


__all__ = ["build_prompts", "get_prompt", "list_specs"]
