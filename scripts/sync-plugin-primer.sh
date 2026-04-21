#!/usr/bin/env bash
# Keep plugins/vaner/prompts/agent-primer.md byte-identical to the canonical
# copy under src/vaner/defaults/prompts/. The plugin is cached to
# ~/.claude/plugins/cache/ by Claude Code, so it must vendor its own copy
# rather than symlink across the tree. CI enforces parity.
set -euo pipefail

SRC="src/vaner/defaults/prompts/agent-primer.md"
DST="plugins/vaner/prompts/agent-primer.md"

usage() {
  echo "usage: $0 --check | --write" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage

case "$1" in
  --check)
    if [[ ! -f "$DST" ]]; then
      echo "missing: $DST (run: $0 --write)" >&2
      exit 1
    fi
    if ! diff -q "$SRC" "$DST" >/dev/null; then
      echo "out of sync: $SRC vs $DST" >&2
      echo "run: $0 --write" >&2
      exit 1
    fi
    ;;
  --write)
    mkdir -p "$(dirname "$DST")"
    cp "$SRC" "$DST"
    ;;
  *)
    usage
    ;;
esac
