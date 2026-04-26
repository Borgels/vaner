# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any

from vaner.setup.questions import setup_questions_for_http, setup_questions_for_mcp


def _normalise_mcp_question(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": question["id"],
        "text": question["prompt"],
        "kind": question["kind"],
        "default": question["default"],
        "options": question["options"],
    }


def _normalise_http_question(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": question["id"],
        "text": question["title"],
        "kind": question["kind"],
        "default": question["default"],
        "options": question["choices"],
    }


def test_setup_question_content_matches_between_mcp_and_http_shapes() -> None:
    mcp_questions = setup_questions_for_mcp()
    http_payload = setup_questions_for_http()

    assert http_payload["version"] == 1
    assert [_normalise_mcp_question(question) for question in mcp_questions] == [
        _normalise_http_question(question) for question in http_payload["questions"]
    ]


def test_setup_question_projections_return_fresh_mutable_payloads() -> None:
    mcp_questions = setup_questions_for_mcp()
    http_payload = setup_questions_for_http()

    mcp_questions[0]["default"].append("changed")
    mcp_questions[0]["options"][0]["label"] = "changed"
    http_payload["questions"][0]["default"].append("changed")
    http_payload["questions"][0]["choices"][0]["label"] = "changed"

    assert setup_questions_for_mcp()[0]["default"] == ["mixed"]
    assert setup_questions_for_mcp()[0]["options"][0]["label"] == "Writing — drafting, editing, narrative"
    assert setup_questions_for_http()["questions"][0]["default"] == ["mixed"]
    assert setup_questions_for_http()["questions"][0]["choices"][0]["label"] == "Writing — drafting, editing, narrative"
