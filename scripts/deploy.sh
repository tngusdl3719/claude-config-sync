#!/bin/bash
# Copies the engine and the mod into the shared repo's mods/config-sync (the next sync sends it).
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
target=${DEPLOY_TARGET:-$HOME/.claude/shared/mods/config-sync}

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/.claude-plugin" "$stage/engine/claude_sync" "$stage/bin"
cp "$root/mod/.claude-plugin/plugin.json" "$stage/.claude-plugin/"
cp -R "$root/mod/hooks" "$root/mod/types" "$stage/"
cp "$root/bin/claude-sync" "$stage/bin/"
chmod 755 "$stage/bin/claude-sync"
cp "$root/engine/main.py" "$stage/engine/"
cp "$root"/engine/claude_sync/*.py "$stage/engine/claude_sync/"

mkdir -p "$target"
rsync -a --delete --exclude '.claude-plugin/types/' --exclude '__pycache__/' "$stage/" "$target/"
echo "deployed to $target"
