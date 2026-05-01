#!/usr/bin/env bash
#
# Phase D-1 smoke: run `vaner launch <client>` for each supported CLI
# client and assert the leverage layers landed where expected.
#
# Layer expectations as of this image's vaner version:
#
#   Claude Code: mcp + primer + skill (no auto-installed plugin yet —
#                that lives in plugins/vaner/ in the source repo and
#                ships through Claude Code's marketplace, not the
#                launcher).
#   Codex CLI:   mcp + primer + skill (hook layer flag-locked
#                upstream; launcher reports it not-applicable).
#
# Exits non-zero on the first missing artefact.

set -euo pipefail

REPO=${REPO:-/home/vaner/sample-repo}
HOME_DIR=${HOME:-/home/vaner}

cd "$REPO"

# Fresh state — anything a previous run wrote.
rm -rf .claude .cursor .clinerules .windsurf .roo .continue .zed
rm -f AGENTS.md .cursorrules
rm -rf "$HOME_DIR/.claude" "$HOME_DIR/.codex" "$HOME_DIR/.cursor"
rm -f  "$HOME_DIR/.claude.json"

assert_file() {
  if [[ ! -f "$1" ]]; then
    echo "FAIL: missing file $1" >&2
    return 1
  fi
  echo "  ok  $1"
}

assert_jq() {
  # assert_jq <file> <jq-expr-that-must-be-non-null>
  local file=$1 expr=$2
  if [[ ! -f "$file" ]]; then
    echo "FAIL: missing file $file (needed for $expr)" >&2
    return 1
  fi
  if ! jq -e "$expr" "$file" >/dev/null 2>&1; then
    echo "FAIL: $file does not satisfy $expr" >&2
    jq . "$file" >&2 || cat "$file" >&2
    return 1
  fi
  echo "  ok  $file :: $expr"
}

echo "==> vaner launch claude-code"
vaner launch claude-code
assert_jq   "$HOME_DIR/.claude.json"                                              '.mcpServers.vaner.command'
assert_file "$REPO/.claude/CLAUDE.md"
assert_file "$HOME_DIR/.claude/skills/vaner/vaner-feedback/SKILL.md"

echo
echo "==> vaner launch codex-cli"
vaner launch codex-cli
# Codex stores MCP in TOML; just check the file exists and mentions vaner.
test -f "$HOME_DIR/.codex/config.toml" \
  && grep -q "vaner" "$HOME_DIR/.codex/config.toml" \
  && echo "  ok  $HOME_DIR/.codex/config.toml :: contains vaner" \
  || { echo "FAIL: codex config missing or no vaner entry" >&2; exit 1; }
assert_file "$REPO/AGENTS.md"
assert_file "$HOME_DIR/.codex/skills/vaner-feedback/SKILL.md"

echo
echo "All Phase D-1 client launches succeeded."
