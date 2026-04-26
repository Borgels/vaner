#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Vaner Claude Code UserPromptSubmit hook translator (0.8.7 WS7).

Reads the hook's stdin JSON, builds a DraftIntentSnapshot for the
Vaner daemon at level L0 (submit-time only), and POSTs it to
``http://127.0.0.1:<port>/signals/composer``.

Privacy / truthful-state contract:

- The raw prompt text is hashed (sha256) and only the hash + length are
  transmitted. The text itself NEVER crosses the adapter boundary.
- L0 means we only emit ``lifecycle_state="submitted"``. The contract
  forbids fabricating earlier states (observing/tentative/etc.) at L0.
- Any failure path (daemon unreachable, malformed input, network
  timeout) exits 0 silently so a misconfigured daemon never blocks the
  user's prompt. Telemetry of the failure goes to stderr only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

# Use ``timezone.utc`` (3.8+) rather than ``datetime.UTC`` (3.11+) so
# the script imports cleanly on system Python 3.10 (still common on
# older macOS / Linux distros). The Vaner CLI itself requires 3.11+,
# but the hook is invoked via the host's ``python3`` which may resolve
# to an older interpreter.
from datetime import datetime, timezone

DEFAULT_DAEMON_PORT = 8473
HTTP_TIMEOUT_SECONDS = 1.0


def _read_payload() -> dict[str, object]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _build_snapshot(payload: dict[str, object]) -> dict[str, object] | None:
    """Translate the Claude Code hook payload into a DraftIntentSnapshot.

    Returns None when the payload lacks the prompt field (nothing to
    hash). The hook always exits 0 in that case.
    """
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return None
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "")
    cwd = str(payload.get("cwd") or "")
    text_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    snapshot: dict[str, object] = {
        "session_id": session_id or f"unknown-{uuid.uuid4().hex[:8]}",
        "snapshot_id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),  # noqa: UP017 — keep 3.8+ compat (see import note)
        "lifecycle_state": "submitted",
        "text_hash": text_hash,
        "length_chars": len(prompt),
        "capabilities": {
            "level": "L0",
            "emits": ["submitted"],
            "host_app": "claude-code",
            "host_kind": "ai_chat_client",
        },
        "field_role": "agent_prompt",
    }
    if cwd:
        snapshot["workspace_id"] = cwd
    return snapshot


def _daemon_url() -> str:
    port_str = os.environ.get("VANER_DAEMON_PORT") or str(DEFAULT_DAEMON_PORT)
    try:
        port = int(port_str)
    except ValueError:
        port = DEFAULT_DAEMON_PORT
    return f"http://127.0.0.1:{port}/signals/composer"


def _post(url: str, body: dict[str, object]) -> tuple[int, str]:
    encoded = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


def main() -> int:
    payload = _read_payload()
    snapshot = _build_snapshot(payload)
    if snapshot is None:
        # No prompt field — nothing to do. Exit silently.
        return 0
    status, body = _post(_daemon_url(), snapshot)
    if status != 200:
        # Best-effort: log to stderr for debugging without blocking the
        # user's prompt.
        sys.stderr.write(f"vaner-composer-submit: daemon returned status={status} body={body[:200]!r}\n")
    return 0


if __name__ == "__main__":
    # 0.8.7 hardening M3: every uncaught exception is swallowed and the
    # script exits 0. The hook contract is "never block the user's
    # prompt" — that means a misconfigured Python, a missing module,
    # or any unforeseen exception MUST NOT propagate as a non-zero exit.
    # Diagnostic detail goes to stderr only.
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as _exc:  # noqa: BLE001 — defense-in-depth top-level
        try:
            sys.stderr.write(f"vaner-composer-submit: unexpected {type(_exc).__name__}: {_exc}\n")
        except Exception:
            # stderr may be closed under unusual hook environments;
            # the hook contract still requires exit 0.
            pass
        raise SystemExit(0) from None
