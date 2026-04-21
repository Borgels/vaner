#!/usr/bin/env bash
# Vaner SessionStart hook.
#
# On every session:
#   1. Detect whether the `vaner` CLI is on PATH.
#   2. Emit SessionStart additionalContext with:
#        - the canonical Vaner usage primer when vaner is installed, so the
#          model actually uses the MCP tools well (prepared context early,
#          search/expand as fallback/branch, feedback at the end), or
#        - installer pointers (the curl one-liner and `/vaner:install`) when
#          the CLI is missing so the MCP server can't start.
# Never auto-runs network fetches; always exits 0 so session startup is not
# blocked.
set -u

# Located relative to the plugin root. `${CLAUDE_PLUGIN_ROOT}` is exported by
# Claude Code when the hook is invoked from the plugin loader.
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PRIMER_FILE="${PLUGIN_ROOT}/prompts/agent-primer.md"

# Python 3.8+ is ubiquitous on macOS/Linux; Vaner itself requires 3.11+. Using
# python3 avoids taking a jq dependency just to build a small JSON document.
if command -v vaner >/dev/null 2>&1; then
  # vaner on PATH — inject the canonical primer so the model uses Vaner well.
  if [[ ! -f "${PRIMER_FILE}" ]]; then
    # Primer missing (should never happen in a properly shipped plugin).
    # Exit silently rather than disrupting the session.
    exit 0
  fi
  PRIMER_FILE="${PRIMER_FILE}" python3 - <<'PY'
import json
import os
import sys

primer = open(os.environ["PRIMER_FILE"], encoding="utf-8").read().strip()

payload = {
    "continue": True,
    "suppressOutput": True,
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": primer,
    },
}

json.dump(payload, sys.stdout)
sys.stdout.write("\n")
PY
  exit 0
fi

# vaner NOT on PATH — point the user at the installer.
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
