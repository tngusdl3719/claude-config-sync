#!/bin/bash
# Packs an app into a dmg that opens to the app beside an Applications shortcut: drag it across to install.
#   bash scripts/make-dmg.sh "dist/Claude Sync.app" dist/ClaudeSync-0.6.0.dmg
set -euo pipefail
app=${1:?usage: make-dmg.sh <app> <out.dmg>}
out=${2:?usage: make-dmg.sh <app> <out.dmg>}
[ -d "$app" ] || { echo "✗ no such app: $app" >&2; exit 1; }
name=$(basename "$app" .app)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
ditto "$app" "$stage/$name.app"
ln -s /Applications "$stage/Applications"
rm -f "$out"
hdiutil create -quiet -volname "$name" -srcfolder "$stage" -fs HFS+ -format UDZO -ov "$out"
