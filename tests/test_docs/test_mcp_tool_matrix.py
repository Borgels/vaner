from __future__ import annotations

import re
from pathlib import Path

FORBIDDEN_TOOL_PATTERNS = [
    re.compile(r"`legacy_get_context`"),
    re.compile(r"`legacy_precompute`"),
    re.compile(r"`legacy_get_metrics`"),
    re.compile(r"`get_context`"),
    re.compile(r"`precompute`"),
    re.compile(r"`get_metrics`"),
    re.compile(r'"name"\s*:\s*"legacy_get_context"'),
    re.compile(r'"name"\s*:\s*"legacy_precompute"'),
    re.compile(r'"name"\s*:\s*"legacy_get_metrics"'),
    re.compile(r'"name"\s*:\s*"get_context"'),
    re.compile(r'"name"\s*:\s*"precompute"'),
    re.compile(r'"name"\s*:\s*"get_metrics"'),
]


def test_docs_do_not_reference_removed_mcp_tools() -> None:
    docs_root = Path(__file__).resolve().parents[3] / "vaner-docs"
    if not docs_root.exists():
        return
    violations: list[str] = []
    for path in docs_root.glob("content/docs/**/*.mdx"):
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_TOOL_PATTERNS:
            if pattern.search(text):
                violations.append(f"{path}: {pattern.pattern}")
    assert violations == []
