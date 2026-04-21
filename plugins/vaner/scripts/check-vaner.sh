#!/usr/bin/env bash
# Vaner SessionStart hook.
#
# Detects whether the `vaner` CLI is on PATH. If present, exits silently so the
# bundled MCP server can start. If missing, emits SessionStart additionalContext
# pointing the user at the canonical installer and the `/vaner:install` skill.
# Never auto-runs network fetches; always exits 0 so session startup is not
# blocked.
set -u

if command -v vaner >/dev/null 2>&1; then
  exit 0
fi

# Python 3.8+ is ubiquitous on macOS/Linux; Vaner itself requires 3.11+. Using
# python3 avoids taking a jq dependency just to build a small JSON document.
python3 - <<'PY'
import json
import sys

message = (
    "The Vaner plugin is enabled, but the `vaner` CLI is not on PATH, so the "
    "bundled MCP server cannot start. Vaner provides predictive context tools "
    "(`mcp__vaner__search`, `mcp__vaner__resolve`, `mcp__vaner__expand`, "
    "`mcp__vaner__feedback`, and more).\n\n"
    "To install Vaner, the user can run:\n\n"
    "    curl -fsSL https://vaner.ai/install.sh | bash -s -- --yes\n\n"
    "Or, with explicit consent, invoke `/vaner:install` which wraps the same "
    "installer behind the Bash tool's permission prompt.\n\n"
    "After installing, restart Claude Code so the MCP server is registered."
)

payload = {
    "continue": True,
    "suppressOutput": True,
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": message,
    },
}

json.dump(payload, sys.stdout)
sys.stdout.write("\n")
PY

exit 0
