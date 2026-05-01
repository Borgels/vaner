#!/usr/bin/env python3
"""Cursor beforeSubmitPrompt hook for the Vaner plugin.

Mirrors the Claude Code plugin's ``composer_submit.py`` — fires on
every prompt submit and POSTs a DraftIntentSnapshot to the local
Vaner daemon's loopback endpoint so the engine pre-fetches prepared
work for the upcoming turn.

Cursor's beforeSubmitPrompt hook payload (per cursor.com/docs/hooks)
arrives on stdin as JSON with at least:

    {
      "prompt": "<the user's draft prompt>",
      "session_id": "<cursor session id>",
      "timestamp": <epoch>,
      ...
    }

We forward those fields to the daemon's ``/signals/composer`` endpoint
(introduced in Vaner 0.8.7 WS3) so prepared work warms up before the
agent starts processing.

Hook output contract: an empty JSON object (or no output) means
"don't modify the request." We never block — this is observational —
and we exit 0 on every failure path so a slow / down daemon never
blocks the user's prompt submission.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DAEMON_BASE_URL = os.environ.get("VANER_DAEMON_URL", "http://127.0.0.1:8473")
COMPOSER_ENDPOINT = "/signals/composer"
HTTP_TIMEOUT_SECONDS = 1.5


def _read_event() -> dict:
    """Parse the Cursor hook event JSON from stdin.

    Returns an empty dict on parse error so the hook still emits a
    valid no-op response.
    """

    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _post_composer_signal(prompt: str, session_id: str) -> None:
    """POST a minimal DraftIntentSnapshot to the daemon. Best-effort."""

    payload = {
        "kind": "composer_lifecycle",
        "source": "cursor-plugin",
        "session_id": session_id,
        "prompt_preview": prompt[:512],
        "prompt_length": len(prompt),
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        DAEMON_BASE_URL + COMPOSER_ENDPOINT,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(  # noqa: S310 — loopback POST only
            request, timeout=HTTP_TIMEOUT_SECONDS
        )
    except (urllib.error.URLError, TimeoutError, OSError):
        # Daemon down / endpoint unavailable — never block the prompt.
        return


def main() -> int:
    event = _read_event()
    prompt = str(event.get("prompt") or "")
    session_id = str(event.get("session_id") or "")
    if prompt:
        _post_composer_signal(prompt, session_id)
    # Empty observational response — pass-through.
    sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
