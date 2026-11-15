#!/bin/bash
# Builds dist/Claude Sync.app (a Dock app with a menu bar icon; ad-hoc signed, engine and mod inside) and
# dist/ClaudeSync-<version>.dmg with the Command Line Tools' Swift; no Xcode needed. The version comes from
# AppInfo.swift. Nothing secret goes in: anyone may download this app (a Notion public connection is read
# from the keychain of the Mac that has one, see scripts/set-notion-oauth.sh).
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd -P)
version=$(sed -n 's/.*static let version = "\(.*\)".*/\1/p' "$root/app/Sources/ClaudeSyncCore/AppInfo.swift")
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

# The Dock icon (drawn by scripts/make-icon.swift), and the script that swaps an update in.
cp "$root/app/Resources/AppIcon.icns" "$app/Contents/Resources/AppIcon.icns"
cp "$root/app/Resources/install-update.sh" "$app/Contents/Resources/install-update.sh"
chmod 755 "$app/Contents/Resources/install-update.sh"

# The engine and the /sync mod, laid out like ~/.claude/shared/mods/config-sync, used before setup.
engine="$app/Contents/Resources/engine"
mkdir -p "$engine/.claude-plugin" "$engine/bin" "$engine/engine/claude_sync"
cp "$root/mod/.claude-plugin/plugin.json" "$engine/.claude-plugin/"
cp -R "$root/mod/hooks" "$root/mod/types" "$engine/"
cp "$root/bin/claude-sync" "$engine/bin/"
chmod 755 "$engine/bin/claude-sync"
cp "$root/engine/main.py" "$engine/engine/"
cp "$root"/engine/claude_sync/*.py "$engine/engine/claude_sync/"

codesign --force --sign - --timestamp=none "$app"
bash "$root/scripts/make-dmg.sh" "$app" "$dist/ClaudeSync-$version.dmg"
echo "built: $app"
echo "dmg:   $dist/ClaudeSync-$version.dmg"
