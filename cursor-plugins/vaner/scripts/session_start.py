#!/usr/bin/env python3
"""Cursor sessionStart hook for the Vaner plugin.

Mirrors the Claude Code plugin's ``check-vaner.sh`` shape but emits
Cursor's hook output format. On every session:

* Detect whether the ``vaner`` CLI is on PATH.
* If yes, inject the canonical Vaner usage primer as ``additional_context``
  so the model uses the MCP tools well (resolve early, search/expand as
  fallback, feedback at end). Also probes the cockpit at
  http://127.0.0.1:8473/status — if it answers, append a single-line
  hint so the model can point the user at the live pipeline view.
* If no, inject installer pointers as ``user_message`` so the user can
  see why the plugin's MCP server isn't starting.

Cursor hook output contract (per cursor.com/docs/hooks):
    Observational hooks emit ``{"additional_context": "...",
    "user_message": "..."}`` to stdout. Empty / missing keys are no-ops.

Always exits 0 — failures must not block session start.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.error
import urllib.request

PLUGIN_ROOT = os.environ.get("CURSOR_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIMER_PATH = os.path.join(PLUGIN_ROOT, "rules", "vaner-primer.mdc")


_PRIMER_FRONTMATTER_END = "---\n"


def _load_primer_body() -> str:
    """Return the primer markdown body without the .mdc YAML frontmatter."""

    try:
        text = open(PRIMER_PATH, encoding="utf-8").read()
    except OSError:
        return ""
    # Strip the leading ``---\n...---\n`` frontmatter; everything after
    # the second delimiter is the body.
    if not text.startswith(_PRIMER_FRONTMATTER_END):
        return text.strip()
    end_idx = text.find(_PRIMER_FRONTMATTER_END, len(_PRIMER_FRONTMATTER_END))
    if end_idx == -1:
        return text.strip()
    return text[end_idx + len(_PRIMER_FRONTMATTER_END) :].strip()


def _cockpit_reachable(timeout: float = 1.0) -> bool:
    """Probe the local cockpit. Returns False on any error within ``timeout``."""

    try:
        with urllib.request.urlopen(  # noqa: S310 — loopback only
            "http://127.0.0.1:8473/status", timeout=timeout
        ) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def main() -> int:
    # Drain stdin (Cursor sends a JSON event payload but this hook
    # doesn't need its content); ignore parse errors.
    try:
        sys.stdin.read()
    except OSError:
        pass

    if shutil.which("vaner") is not None:
        primer = _load_primer_body()
        if not primer:
            # Primer file missing — silently no-op rather than
            # disrupting session start.
            _emit({})
            return 0
        cockpit_hint = ""
        if _cockpit_reachable():
            cockpit_hint = (
                "\n\nLive Vaner state is available at http://127.0.0.1:8473/ "
                "(cockpit is up). Mention this if the user asks about "
                "prediction state, scenario queue depth, or wants to see "
                "the live pipeline view."
            )
        _emit({"additional_context": primer + cockpit_hint})
        return 0

    # vaner NOT on PATH — surface a user-visible message.
    message = (
        "The Vaner Cursor plugin is enabled but the `vaner` CLI is not on "
        "PATH, so the bundled MCP server cannot start. Install Vaner "
        "with:\n\n"
        "    curl -fsSL https://vaner.ai/install.sh | bash -s -- --yes\n\n"
        "Or download Vaner Desktop at https://vaner.ai and re-open Cursor."
    )
    _emit({"user_message": message})
    return 0


if __name__ == "__main__":
    sys.exit(main())
