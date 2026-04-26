# SPDX-License-Identifier: Apache-2.0
"""Canonical Simple-Mode setup questions.

MCP and daemon HTTP intentionally expose slightly different public key
names for the same five questions. Keep the ordered question data here
and project it into each wire shape at the boundary.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

QuestionKind = Literal["single", "multi"]
QuestionDefault = str | list[str]


class _QuestionOption(TypedDict):
    value: str
    label: str


class _SetupQuestion(TypedDict):
    id: str
    text: str
    kind: QuestionKind
    default: QuestionDefault
    options: tuple[_QuestionOption, ...]


_SETUP_QUESTIONS_VERSION = 1

_SETUP_QUESTIONS: tuple[_SetupQuestion, ...] = (
    {
        "id": "work_styles",
        "text": "What kind of work do you want help with?",
        "kind": "multi",
        "default": ["mixed"],
        "options": (
            {"value": "writing", "label": "Writing — drafting, editing, narrative"},
            {"value": "research", "label": "Research — surveys, deep reading, citations"},
            {"value": "planning", "label": "Planning — design docs, roadmaps, project layout"},
            {"value": "support", "label": "Support — answering questions, troubleshooting"},
            {"value": "learning", "label": "Learning — studying, exploring a new domain"},
            {"value": "coding", "label": "Coding — software development"},
            {"value": "general", "label": "General — knowledge work, mixed light tasks"},
            {"value": "mixed", "label": "Mixed — a bit of everything (safe default)"},
            {"value": "unsure", "label": "Unsure — I'd rather Vaner picks for me"},
        ),
    },
    {
        "id": "priority",
        "text": "What matters most?",
        "kind": "single",
        "default": "balanced",
        "options": (
            {"value": "balanced", "label": "Balanced — a sensible middle"},
            {"value": "speed", "label": "Speed — snappy responses"},
            {"value": "quality", "label": "Quality — best answer, even if slow"},
            {"value": "privacy", "label": "Privacy — keep data on this machine"},
            {"value": "cost", "label": "Cost — minimise spend"},
            {"value": "low_resource", "label": "Low-resource — go easy on this machine"},
        ),
    },
    {
        "id": "compute_posture",
        "text": "How hard should this machine work for you?",
        "kind": "single",
        "default": "balanced",
        "options": (
            {"value": "light", "label": "Light — barely use the CPU/GPU"},
            {"value": "balanced", "label": "Balanced — work with what's idle"},
            {"value": "available_power", "label": "Available-power — use what this box has"},
        ),
    },
    {
        "id": "cloud_posture",
        "text": "How do you feel about cloud LLMs?",
        "kind": "single",
        "default": "ask_first",
        "options": (
            {"value": "local_only", "label": "Local only — never reach for cloud LLMs"},
            {"value": "ask_first", "label": "Ask first — confirm before any cloud call"},
            {"value": "hybrid_when_worth_it", "label": "Hybrid — cloud when it's clearly worth it"},
            {"value": "best_available", "label": "Best available — use the best model for the job"},
        ),
    },
    {
        "id": "background_posture",
        "text": "How aggressive should background pondering be?",
        "kind": "single",
        "default": "normal",
        "options": (
            {"value": "minimal", "label": "Minimal — barely ponder when idle"},
            {"value": "normal", "label": "Normal — moderate background pondering"},
            {"value": "idle_more", "label": "Idle-more — ponder broadly when the box is idle"},
            {"value": "deep_run_aggressive", "label": "Deep-Run-aggressive — happy to run overnight"},
        ),
    },
)


def _copy_default(default: QuestionDefault) -> QuestionDefault:
    return list(default) if isinstance(default, list) else default


def _copy_options(question: _SetupQuestion) -> list[dict[str, str]]:
    return [dict(option) for option in question["options"]]


def setup_questions_for_mcp() -> list[dict[str, Any]]:
    """Return the MCP ``vaner.setup.questions`` wire shape."""

    return [
        {
            "id": question["id"],
            "prompt": question["text"],
            "kind": question["kind"],
            "default": _copy_default(question["default"]),
            "options": _copy_options(question),
        }
        for question in _SETUP_QUESTIONS
    ]


def setup_questions_for_http() -> dict[str, Any]:
    """Return the daemon ``GET /setup/questions`` wire shape."""

    return {
        "version": _SETUP_QUESTIONS_VERSION,
        "questions": [
            {
                "id": question["id"],
                "title": question["text"],
                "kind": question["kind"],
                "default": _copy_default(question["default"]),
                "choices": _copy_options(question),
            }
            for question in _SETUP_QUESTIONS
        ],
    }
