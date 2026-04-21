#!/usr/bin/env bash
# Keep the Claude Code plugin's version aligned with pyproject.toml.
#
# Sources of truth:
#   pyproject.toml::project.version  (authoritative)
#   plugins/vaner/.claude-plugin/plugin.json::version
#   .claude-plugin/marketplace.json::plugins[0].version
#
# Claude Code uses plugin.json::version to decide whether to propagate updates
# to installed users, so drift silently strands them on old versions.
set -euo pipefail

usage() {
  echo "usage: $0 --check | --write" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage

MODE="$1"
case "$MODE" in
  --check|--write) ;;
  *) usage ;;
esac

PLUGIN_JSON="plugins/vaner/.claude-plugin/plugin.json"
MARKETPLACE_JSON=".claude-plugin/marketplace.json"

python3 - "$MODE" "$PLUGIN_JSON" "$MARKETPLACE_JSON" <<'PY'
import json
import pathlib
import sys
import tomllib

mode, plugin_path, marketplace_path = sys.argv[1], sys.argv[2], sys.argv[3]

pyproject = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
source_version = pyproject["project"]["version"]

def load(path: str) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))

def dump(path: str, doc: dict) -> None:
    pathlib.Path(path).write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

plugin = load(plugin_path)
marketplace = load(marketplace_path)

def plugin_entry(doc: dict) -> dict:
    for entry in doc.get("plugins", []):
        if entry.get("name") == "vaner":
            return entry
    raise SystemExit(f"no 'vaner' entry in {marketplace_path}")

entry = plugin_entry(marketplace)

if mode == "--check":
    errors = []
    if plugin.get("version") != source_version:
        errors.append(
            f"{plugin_path}::version = {plugin.get('version')!r}, "
            f"expected {source_version!r}"
        )
    if entry.get("version") != source_version:
        errors.append(
            f"{marketplace_path}::plugins[name=vaner].version = "
            f"{entry.get('version')!r}, expected {source_version!r}"
        )
    if errors:
        print("version parity failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(f"run: scripts/bump-plugin-version.sh --write", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)

plugin["version"] = source_version
entry["version"] = source_version
dump(plugin_path, plugin)
dump(marketplace_path, marketplace)
print(f"synced plugin version to {source_version}")
PY
