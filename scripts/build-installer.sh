#!/bin/bash
# Builds dist/claude-sync-install.command: the installer with the engine and mod packed in at its end,
# so a Notion-backed PC (no git clone to take the engine from) can set itself up.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
out=${OUT:-$root/dist/claude-sync-install.command}

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
DEPLOY_TARGET="$stage/config-sync" bash "$root/scripts/deploy.sh" >/dev/null
mkdir -p "$(dirname "$out")"
{
  cat "$root/installer/claude-sync-install.command"
  printf '__CLAUDE_SYNC_ENGINE__\n'
  tar -czf - -C "$stage" config-sync | /usr/bin/base64 -b 76
} > "$out.tmp"
chmod 755 "$out.tmp"
mv "$out.tmp" "$out"
echo "built $out"
