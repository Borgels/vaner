#!/usr/bin/env python3
"""Composer-submit hook script — installed into per-client hook surfaces
by :mod:`vaner.cli.commands.hooks`.

Fires on the user's prompt-submit event in Cline (``UserPromptSubmit``)
and Windsurf (``user_prompt``). Reads the hook event JSON from stdin
and POSTs a DraftIntentSnapshot to the local Vaner daemon's
``/signals/composer`` endpoint so prepared work warms up before the
agent starts processing the prompt.

Hook output contract is observational across both clients:

* **Cline**: emits ``{}`` (empty JSON) on success — no cancel, no
  context modification, no error. The hook never blocks; failures
  silently no-op.
* **Windsurf**: exits 0 with no output on success. Pre-hooks block by
  exiting with code 2; we never want to block, so we always exit 0.

Designed to be drop-in across both clients — same script, same
contract — so :mod:`vaner.cli.commands.hooks` can ship one canonical
file and just point per-client config at it.

Source: docs.vaner.ai/integrations/client-capabilities
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
SOURCE = os.environ.get("VANER_HOOK_SOURCE", "vaner-hook")


def _read_event() -> dict:
    """Parse the hook event JSON from stdin. Returns ``{}`` on parse error."""

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


def _extract_prompt(event: dict) -> tuple[str, str]:
    """Pull (prompt, session_id) out of the event payload.

    Each client uses slightly different keys; we accept the union so
    one script works across both Cline and Windsurf without per-client
    branching.
    """

    prompt_keys = ("prompt", "user_prompt", "input", "message", "text")
    session_keys = ("session_id", "sessionId", "task_id", "taskId")
    prompt = ""
    session = ""
    for key in prompt_keys:
        value = event.get(key)
        if isinstance(value, str) and value:
            prompt = value
            break
    for key in session_keys:
        value = event.get(key)
        if isinstance(value, str) and value:
            session = value
            break
    return prompt, session


def _post_composer_signal(prompt: str, session_id: str) -> None:
    """POST a minimal DraftIntentSnapshot to the daemon. Best-effort."""

    payload = {
        "kind": "composer_lifecycle",
        "source": SOURCE,
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
    prompt, session_id = _extract_prompt(event)
    if prompt:
        _post_composer_signal(prompt, session_id)
    # Empty JSON object is a valid no-op for Cline; Windsurf accepts
    # it as well (unused output is ignored in observational hooks).
    sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
