#!/bin/bash
# Builds dist/Claude Sync.app (a Dock app with a menu bar icon; ad-hoc signed, engine and mod inside) and dist/ClaudeSync-<version>.zip
# with the Command Line Tools' Swift; no Xcode needed. The version comes from AppInfo.swift.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
version=$(sed -n 's/.*version = "\(.*\)".*/\1/p' "$root/app/Sources/ClaudeSyncCore/AppInfo.swift")
dist="$root/dist"
app="$dist/Claude Sync.app"

cd "$root/app"
swift build -c release --product ClaudeSync
bin="$(swift build -c release --show-bin-path)/ClaudeSync"

rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$bin" "$app/Contents/MacOS/ClaudeSync"
cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>ClaudeSync</string>
  <key>CFBundleIdentifier</key><string>com.suhyeon.claudesync</string>
  <key>CFBundleName</key><string>Claude Sync</string>
  <key>CFBundleDisplayName</key><string>Claude Sync</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

# The Dock icon (drawn by scripts/make-icon.swift).
cp "$root/app/Resources/AppIcon.icns" "$app/Contents/Resources/AppIcon.icns"

# The engine and the /sync mod, laid out like ~/.claude/shared/mods/config-sync, used before setup.
engine="$app/Contents/Resources/engine"
mkdir -p "$engine/.claude-plugin" "$engine/bin" "$engine/engine/claude_sync"
cp "$root/mod/.claude-plugin/plugin.json" "$engine/.claude-plugin/"
cp -R "$root/mod/hooks" "$root/mod/types" "$engine/"
cp "$root/bin/claude-sync" "$engine/bin/"
chmod 755 "$engine/bin/claude-sync"
cp "$root/engine/main.py" "$engine/engine/"
cp "$root"/engine/claude_sync/*.py "$engine/engine/claude_sync/"

# The Notion public connection for 'Notion으로 연결' (personal builds only; scripts/set-notion-oauth.sh
# stores it in the keychain: account = client id, password = client secret). Without it the app offers
# only the secret-key way. The secret goes through stdin, never argv.
oauth_item=${CLAUDE_SYNC_OAUTH_ITEM:-claude-sync-notion-oauth}
client_id=$(security find-generic-password -s "$oauth_item" 2>/dev/null | sed -n 's/.*"acct"<blob>="\(.*\)"/\1/p' || true)
if [ -n "$client_id" ]; then
  security find-generic-password -s "$oauth_item" -w \
    | /usr/bin/python3 -I -c 'import json, sys; print(json.dumps({"client_id": sys.argv[1], "client_secret": sys.stdin.read().strip()}))' "$client_id" \
    > "$app/Contents/Resources/notion-oauth.json"
  chmod 600 "$app/Contents/Resources/notion-oauth.json"
  echo "Notion 연결: 포함 ($client_id)"
else
  echo "Notion 연결: 없음 (비밀 키 방식만)"
fi

codesign --force --sign - --timestamp=none "$app"
rm -f "$dist/ClaudeSync-$version.zip"
(cd "$dist" && ditto -c -k --norsrc --noextattr --noacl --keepParent "Claude Sync.app" "ClaudeSync-$version.zip")
echo "built: $app"
echo "zip:   $dist/ClaudeSync-$version.zip"
