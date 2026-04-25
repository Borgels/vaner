# SPDX-License-Identifier: Apache-2.0
"""WS7 — `vaner.setup.*` and `vaner.policy.show` MCP tool tests (0.8.6)."""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp")
from mcp.types import CallToolRequest, ListToolsRequest

from vaner.mcp.server import build_server


def _seed_repo(repo_root) -> None:
    (repo_root / ".vaner").mkdir(parents=True, exist_ok=True)
    (repo_root / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )


def _payload(result) -> dict:
    return json.loads(result.root.content[0].text)


def _call(server, name: str, arguments: dict | None = None) -> dict:
    handler = server.request_handlers[CallToolRequest]
    arguments = arguments or {}

    async def _run() -> dict:
        resp = await handler(CallToolRequest(method="tools/call", params={"name": name, "arguments": arguments}))
        return {"isError": bool(resp.root.isError), "payload": _payload(resp)}

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# vaner.setup.questions
# ---------------------------------------------------------------------------


def test_questions_returns_five_questions(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    out = _call(server, "vaner.setup.questions")
    assert out["isError"] is False
    questions = out["payload"]["questions"]
    assert isinstance(questions, list)
    assert len(questions) == 5
    ids = [q["id"] for q in questions]
    assert ids == [
        "work_styles",
        "priority",
        "compute_posture",
        "cloud_posture",
        "background_posture",
    ]
    # work_styles is multi-select; the rest are single-select.
    by_id = {q["id"]: q for q in questions}
    assert by_id["work_styles"]["kind"] == "multi"
    assert by_id["priority"]["kind"] == "single"
    # Each question has at least two options with value+label.
    for q in questions:
        assert len(q["options"]) >= 2
        for opt in q["options"]:
            assert "value" in opt
            assert "label" in opt


# ---------------------------------------------------------------------------
# vaner.setup.recommend
# ---------------------------------------------------------------------------


def test_recommend_happy_path(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    out = _call(
        server,
        "vaner.setup.recommend",
        {
            "work_styles": ["coding", "research"],
            "priority": "balanced",
            "compute_posture": "balanced",
            "cloud_posture": "ask_first",
            "background_posture": "normal",
        },
    )
    assert out["isError"] is False
    payload = out["payload"]
    assert "bundle" in payload
    assert isinstance(payload["bundle"]["id"], str)
    # The id must match a known catalog id.
    from vaner.setup.catalog import PROFILE_CATALOG

    known_ids = {b.id for b in PROFILE_CATALOG}
    assert payload["bundle"]["id"] in known_ids
    assert "score" in payload
    assert isinstance(payload["reasons"], list)
    assert isinstance(payload["runner_ups"], list)
    assert isinstance(payload["forced_fallback"], bool)


def test_recommend_missing_fields_uses_defaults(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    out = _call(server, "vaner.setup.recommend", {})
    assert out["isError"] is False
    payload = out["payload"]
    assert "bundle" in payload
    # Default work_styles=["mixed"] + balanced/ask_first/normal — selection
    # must produce a real bundle, not fall back to forced_fallback.
    assert isinstance(payload["bundle"]["id"], str)
    assert payload["bundle"]["id"]


def test_recommend_accepts_single_string_work_style(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    out = _call(server, "vaner.setup.recommend", {"work_styles": "coding"})
    assert out["isError"] is False
    assert out["payload"]["bundle"]["id"]


# ---------------------------------------------------------------------------
# vaner.setup.apply
# ---------------------------------------------------------------------------


def test_apply_dry_run_does_not_persist(temp_repo) -> None:
    _seed_repo(temp_repo)
    config_path = temp_repo / ".vaner" / "config.toml"
    before = config_path.read_text(encoding="utf-8")
    server = build_server(temp_repo)
    out = _call(
        server,
        "vaner.setup.apply",
        {
            "answers": {
                "work_styles": ["coding"],
                "priority": "balanced",
                "compute_posture": "balanced",
                "cloud_posture": "ask_first",
                "background_posture": "normal",
            },
            "dry_run": True,
        },
    )
    assert out["isError"] is False
    payload = out["payload"]
    assert payload["written"] is False
    assert payload["block_reason"] is not None
    assert "dry_run" in payload["block_reason"]
    # File must be byte-identical.
    assert config_path.read_text(encoding="utf-8") == before


def test_apply_blocks_on_widening_without_confirm(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    # First pin a local-only bundle.
    out1 = _call(
        server,
        "vaner.setup.apply",
        {"bundle_id": "local_lightweight"},
    )
    assert out1["isError"] is False
    assert out1["payload"]["written"] is True
    # Now ask for a hybrid bundle without confirming the widening.
    out2 = _call(
        server,
        "vaner.setup.apply",
        {"bundle_id": "hybrid_balanced"},
    )
    assert out2["isError"] is False
    payload = out2["payload"]
    assert payload["widens_cloud_posture"] is True
    assert payload["written"] is False
    assert payload["block_reason"] is not None
    assert "WIDENS_CLOUD_POSTURE" in payload["block_reason"]


def test_apply_proceeds_with_confirm(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    _call(server, "vaner.setup.apply", {"bundle_id": "local_lightweight"})
    out = _call(
        server,
        "vaner.setup.apply",
        {"bundle_id": "hybrid_balanced", "confirm_cloud_widening": True},
    )
    assert out["isError"] is False
    payload = out["payload"]
    assert payload["widens_cloud_posture"] is True
    assert payload["written"] is True
    assert payload["block_reason"] is None
    # Verify the on-disk policy section reflects the new id.
    text = (temp_repo / ".vaner" / "config.toml").read_text(encoding="utf-8")
    assert 'selected_bundle_id = "hybrid_balanced"' in text


def test_apply_explicit_bundle_id(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    out = _call(server, "vaner.setup.apply", {"bundle_id": "cost_saver"})
    assert out["isError"] is False
    payload = out["payload"]
    assert payload["bundle_id"] == "cost_saver"
    assert payload["written"] is True
    text = (temp_repo / ".vaner" / "config.toml").read_text(encoding="utf-8")
    assert 'selected_bundle_id = "cost_saver"' in text


def test_apply_unknown_bundle_id_errors(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    out = _call(server, "vaner.setup.apply", {"bundle_id": "definitely_not_real"})
    assert out["isError"] is True
    assert out["payload"]["code"] == "unknown_bundle_id"


def test_apply_requires_answers_or_bundle_id(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    out = _call(server, "vaner.setup.apply", {})
    assert out["isError"] is True
    assert out["payload"]["code"] == "invalid_input"


# ---------------------------------------------------------------------------
# vaner.setup.status
# ---------------------------------------------------------------------------


def test_status_returns_well_formed(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    out = _call(server, "vaner.setup.status")
    assert out["isError"] is False
    payload = out["payload"]
    for key in ("mode", "selected_bundle_id", "completed_at", "applied_policy", "hardware"):
        assert key in payload
    assert payload["mode"] in ("simple", "advanced")
    assert isinstance(payload["selected_bundle_id"], str)
    assert isinstance(payload["applied_policy"], dict)
    assert "bundle_id" in payload["applied_policy"]
    assert "overrides_applied" in payload["applied_policy"]
    assert isinstance(payload["hardware"], dict)
    for hw_key in ("os", "tier", "cpu_class", "ram_gb"):
        assert hw_key in payload["hardware"]


def test_status_after_apply_reflects_new_bundle(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    _call(server, "vaner.setup.apply", {"bundle_id": "cost_saver"})
    out = _call(server, "vaner.setup.status")
    assert out["isError"] is False
    assert out["payload"]["selected_bundle_id"] == "cost_saver"


# ---------------------------------------------------------------------------
# vaner.policy.show
# ---------------------------------------------------------------------------


def test_policy_show_includes_overrides(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    out = _call(server, "vaner.policy.show")
    assert out["isError"] is False
    payload = out["payload"]
    assert "bundle" in payload
    assert "overrides_applied" in payload
    assert isinstance(payload["overrides_applied"], list)
    # Bundle JSON includes the WS6 stable shape.
    bundle = payload["bundle"]
    for key in (
        "id",
        "label",
        "description",
        "local_cloud_posture",
        "runtime_profile",
        "spend_profile",
        "context_injection_default",
        "deep_run_profile",
    ):
        assert key in bundle


def test_policy_show_after_apply_reflects_chosen_bundle(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    _call(server, "vaner.setup.apply", {"bundle_id": "local_lightweight"})
    out = _call(server, "vaner.policy.show")
    assert out["isError"] is False
    assert out["payload"]["bundle"]["id"] == "local_lightweight"


# ---------------------------------------------------------------------------
# Tool list contains all five new tools.
# ---------------------------------------------------------------------------


def test_setup_tools_are_advertised(temp_repo) -> None:
    _seed_repo(temp_repo)
    server = build_server(temp_repo)
    list_handler = server.request_handlers[ListToolsRequest]

    async def _run() -> set[str]:
        listed = await list_handler(ListToolsRequest(method="tools/list"))
        return {t.name for t in listed.root.tools}

    names = asyncio.run(_run())
    for tool_name in (
        "vaner.setup.questions",
        "vaner.setup.recommend",
        "vaner.setup.apply",
        "vaner.setup.status",
        "vaner.policy.show",
    ):
        assert tool_name in names
