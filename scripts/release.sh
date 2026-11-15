#!/bin/bash
# Publishes the version in AppInfo.swift as a GitHub release, which installed apps then take by themselves:
#   ClaudeSync-<version>.dmg, its signature ClaudeSync-<version>.dmg.sig (scripts/update-key.sh) and the
#   Terminal installer. Run it on the Mac that holds the update key, from a clean main that GitHub has.
#   bash scripts/release.sh                    (RELEASE_NOTES="…" for the release text)
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$root"
info=app/Sources/ClaudeSyncCore/AppInfo.swift
version=$(sed -n 's/.*static let version = "\(.*\)".*/\1/p' "$info")
public_key=$(sed -n 's/.*static let updatePublicKey = "\(.*\)".*/\1/p' "$info")
tag="v$version"
fail() { echo "✗ $*" >&2; exit 1; }

[ -n "$version" ] || fail "no version in $info"
[ -z "$(git status --porcelain)" ] || fail "commit or put aside the changes first"
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || fail "HEAD is not origin/main: push first"
gh release view "$tag" >/dev/null 2>&1 && fail "$tag is already released"
[ "$(bash scripts/update-key.sh public)" = "$public_key" ] || fail "this Mac's update key is not the one the app trusts"

bash scripts/build-app.sh
bash scripts/build-installer.sh
dmg="dist/ClaudeSync-$version.dmg"
bash scripts/update-key.sh sign "$dmg"

gh release create "$tag" "$dmg" "$dmg.sig" dist/claude-sync-install.command \
  --target "$(git rev-parse HEAD)" --title "Claude Sync $version" --notes "${RELEASE_NOTES:-Claude Sync $version}"
echo "released $tag"
