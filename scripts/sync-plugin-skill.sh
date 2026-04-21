#!/usr/bin/env bash
# Keep plugins/vaner/skills/vaner-feedback/SKILL.md byte-identical to the
# canonical copy under src/vaner/defaults/skills/. Symlinks break after the
# plugin is copied to ~/.claude/plugins/cache/, so we copy and enforce parity.
set -euo pipefail

SRC="src/vaner/defaults/skills/vaner-feedback/SKILL.md"
DST="plugins/vaner/skills/vaner-feedback/SKILL.md"

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
