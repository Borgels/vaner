#!/usr/bin/env bash
# Vaner UserPromptSubmit hook (0.8.7 WS7 — L0 Composer Adapter).
#
# Fires on every Claude Code prompt submit. Reads the hook's stdin
# JSON payload (which carries the prompt text + session_id) and POSTs
# a DraftIntentSnapshot to the Vaner daemon's loopback endpoint.
#
# This is a thin shell shim — all logic lives in composer_submit.py so
# the implementation is testable. The hook MUST exit 0 quickly under
# every failure mode so a misconfigured daemon never blocks the user's
# prompt; the translator handles that.
set -u

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
exec python3 "${PLUGIN_ROOT}/scripts/composer_submit.py"
