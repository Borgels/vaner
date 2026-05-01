#!/usr/bin/env bash
# Keep plugins/vaner/skills/vaner-feedback/SKILL.md byte-identical to the
# canonical copy under src/vaner/defaults/skills/. Symlinks break after the
# plugin is copied to ~/.claude/plugins/cache/, so we copy and enforce parity.
set -euo pipefail

SRC="src/vaner/defaults/skills/vaner-feedback/SKILL.md"
DSTS=(
  "plugins/vaner/skills/vaner-feedback/SKILL.md"
  "cursor-plugins/vaner/skills/vaner-feedback/SKILL.md"
)

usage() {
  echo "usage: $0 --check | --write" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage

case "$1" in
  --check)
    fail=0
    for dst in "${DSTS[@]}"; do
      if [[ ! -f "$dst" ]]; then
        echo "missing: $dst (run: $0 --write)" >&2
        fail=1
        continue
      fi
      if ! diff -q "$SRC" "$dst" >/dev/null; then
        echo "out of sync: $SRC vs $dst" >&2
        fail=1
      fi
    done
    if [[ $fail -ne 0 ]]; then
      echo "run: $0 --write" >&2
      exit 1
    fi
    ;;
  --write)
    for dst in "${DSTS[@]}"; do
      mkdir -p "$(dirname "$dst")"
      cp "$SRC" "$dst"
    done
    ;;
  *)
    usage
    ;;
esac
