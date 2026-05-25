# SPDX-License-Identifier: Apache-2.0

"""Tests for slash-command-style MCP prompts (`prompts/list`, `prompts/get`).

The prompts are the user-visible front door for terminal hosts (Claude Code,
Codex, Cursor) where `prompts/list` becomes a slash-command palette. They must
stay 1:1 with real `vaner.*` tools and reject missing required arguments.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
    pytest.skip("mcp package is unavailable in this test environment", allow_module_level=True)


def _build(tmp_path: Path):
    (tmp_path / ".vaner").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    from vaner.mcp.server import build_server

    return build_server(tmp_path)


def _list_prompts(server) -> list:
    from mcp.types import ListPromptsRequest

    handler = server.request_handlers[ListPromptsRequest]

    async def _run():
        return await handler(ListPromptsRequest(method="prompts/list"))

    return list(asyncio.run(_run()).root.prompts)


def _get_prompt(server, name: str, arguments: dict[str, str] | None):
    from mcp.types import GetPromptRequest, GetPromptRequestParams

    handler = server.request_handlers[GetPromptRequest]

    async def _run():
        return await handler(
            GetPromptRequest(
                method="prompts/get",
                params=GetPromptRequestParams(name=name, arguments=arguments),
            )
        )

    return asyncio.run(_run()).root


def test_list_prompts_includes_core_slash_commands(tmp_path: Path) -> None:
    server = _build(tmp_path)
    prompts = _list_prompts(server)
    names = {p.name for p in prompts}
    assert {
        "vaner-resolve",
        "vaner-suggest",
        "vaner-dashboard",
        "vaner-prepared",
        "vaner-adopt",
        "vaner-feedback",
        "vaner-status",
    }.issubset(names)


def test_listed_prompts_have_titles_and_descriptions(tmp_path: Path) -> None:
    server = _build(tmp_path)
    for prompt in _list_prompts(server):
        assert prompt.title, f"{prompt.name} missing title"
        assert prompt.description, f"{prompt.name} missing description"


def test_get_prompt_resolve_substitutes_task(tmp_path: Path) -> None:
    server = _build(tmp_path)
    result = _get_prompt(server, "vaner-resolve", {"task": "fix the auth bug"})
    assert len(result.messages) == 1
    msg = result.messages[0]
    assert msg.role == "user"
    text = msg.content.text
    assert "fix the auth bug" in text
    assert "vaner.resolve" in text


def test_get_prompt_adopt_substitutes_id(tmp_path: Path) -> None:
    server = _build(tmp_path)
    result = _get_prompt(server, "vaner-adopt", {"id": "pred_abc123"})
    text = result.messages[0].content.text
    assert "pred_abc123" in text
    assert "vaner.predictions.adopt" in text


def test_get_prompt_feedback_substitutes_all_args(tmp_path: Path) -> None:
    server = _build(tmp_path)
    result = _get_prompt(
        server,
        "vaner-feedback",
        {"resolution_id": "res_42", "verdict": "useful", "correction": "n/a"},
    )
    text = result.messages[0].content.text
    assert "res_42" in text
    assert "useful" in text
    assert "vaner.feedback" in text


def test_get_prompt_no_args_renders_template(tmp_path: Path) -> None:
    server = _build(tmp_path)
    for name, expected_tool in [
        ("vaner-suggest", "vaner.suggest"),
        ("vaner-dashboard", "vaner.predictions.dashboard"),
        ("vaner-prepared", "vaner.prepared_work.dashboard"),
        ("vaner-status", "vaner.status"),
    ]:
        result = _get_prompt(server, name, None)
        assert result.messages
        text = result.messages[0].content.text
        assert expected_tool in text, f"{name} should reference {expected_tool}"


def test_get_prompt_missing_required_argument_raises(tmp_path: Path) -> None:
    server = _build(tmp_path)
    # vaner-resolve requires `task`; vaner-adopt requires `id`.
    with pytest.raises(Exception):
        _get_prompt(server, "vaner-resolve", {})
    with pytest.raises(Exception):
        _get_prompt(server, "vaner-adopt", None)


def test_get_prompt_unknown_name_raises(tmp_path: Path) -> None:
    server = _build(tmp_path)
    with pytest.raises(Exception):
        _get_prompt(server, "vaner-not-a-real-prompt", None)


def test_prompt_specs_match_known_tool_names(tmp_path: Path) -> None:
    """Each prompt template must reference at least one real ``vaner.*`` tool."""
    from vaner.mcp.prompts import list_specs

    expected_tool_by_prompt = {
        "vaner-resolve": "vaner.resolve",
        "vaner-suggest": "vaner.suggest",
        "vaner-dashboard": "vaner.predictions.dashboard",
        "vaner-prepared": "vaner.prepared_work.dashboard",
        "vaner-adopt": "vaner.predictions.adopt",
        "vaner-feedback": "vaner.feedback",
        "vaner-status": "vaner.status",
    }
    for spec in list_specs():
        assert expected_tool_by_prompt[spec.name] in spec.template, (
            f"prompt {spec.name!r} should reference {expected_tool_by_prompt[spec.name]!r}"
        )
