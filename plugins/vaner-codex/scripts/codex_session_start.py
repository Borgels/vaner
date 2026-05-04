#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Codex SessionStart hook for Vaner."""

from __future__ import annotations

import json
import shutil
import sys


def main() -> int:
    if shutil.which("vaner") is None:
        message = "Vaner is not on PATH, so the bundled MCP server and prompt-signal hooks cannot run."
    else:
        message = (
            "Vaner is available. For non-trivial turns, use vaner.suggest as the immediate no-wait turn decision. "
            "Adopt at most one strong matching prepared package, use resolve only when suggested as optional, "
            "and answer normally when Vaner has nothing clearly relevant."
        )
    payload = {
        "continue": True,
        "suppressOutput": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": message,
        },
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
