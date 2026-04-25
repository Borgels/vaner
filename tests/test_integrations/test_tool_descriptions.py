# SPDX-License-Identifier: Apache-2.0

"""Pin the behavior-shaping language in MCP tool descriptions.

These tests guard against accidental regression to the old "one-line"
descriptions that failed to steer backend LLMs toward correct tool use.
"""

from __future__ import annotations


def _tool_descriptions() -> dict[str, str]:
    """Extract the {tool_name: description} map from the MCP server source.

    We parse the source rather than instantiating the server because building
    a real MCP server requires a repo fixture, engine wiring, etc. Descriptions
    are plain string literals in the `Tool(name=..., description=...)` calls.
    Handles both single-line string and multi-line parenthesized forms.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "vaner" / "mcp" / "server.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if func_name != "Tool":
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        name_node = kwargs.get("name")
        desc_node = kwargs.get("description")
        if not isinstance(name_node, ast.Constant) or not isinstance(name_node.value, str):
            continue
        name = name_node.value
        if not name.startswith("vaner."):
            continue
        desc_str: str | None = None
        if isinstance(desc_node, ast.Constant) and isinstance(desc_node.value, str):
            desc_str = desc_node.value
        elif isinstance(desc_node, ast.JoinedStr):
            # f-string: best-effort join of literal parts
            parts: list[str] = []
            for v in desc_node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
            desc_str = "".join(parts)
        else:
            # implicit string concatenation inside parens — ast.Constant already folded
            pass
        if desc_str is not None:
            out[name] = desc_str
    return out


def test_predictions_active_mentions_mechanical_and_adopt_preference() -> None:
    descs = _tool_descriptions()
    d = descs["vaner.predictions.active"]
    assert "Do NOT call mechanically" in d
    assert "prefer vaner.predictions.adopt" in d


def test_predictions_active_lists_readiness_states() -> None:
    d = _tool_descriptions()["vaner.predictions.active"]
    for state in ("queued", "grounding", "evidence_gathering", "drafting", "ready", "stale"):
        assert state in d, f"readiness state {state!r} missing from vaner.predictions.active description"


def test_resolve_mentions_adopted_package_block() -> None:
    d = _tool_descriptions()["vaner.resolve"]
    assert "<VANER_ADOPTED_PACKAGE>" in d
    assert "Do NOT call vaner.resolve in parallel with vaner.predictions.adopt" in d


def test_adopt_mentions_one_per_turn_cap() -> None:
    d = _tool_descriptions()["vaner.predictions.adopt"]
    assert "Adopt at most one prediction per user turn" in d
    assert "adopted_from_prediction_id" in d
    assert "feedback loop" in d.lower()
