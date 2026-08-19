#!/usr/bin/env bash
# Собирает ClaudeRC.app из продукта SPM. Полного Xcode на машине нет, поэтому
# бандл складывается руками, а не через xcodebuild.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="ClaudeRCMenu"
APP="$HERE/build/ClaudeRC.app"

# Версия одна на тулзу и приложение, иначе они разъедутся.
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$HERE/../pyproject.toml" | head -1)"
[ -n "$VERSION" ] || { echo "версия не найдена в pyproject.toml" >&2; exit 1; }

swift build -c release --package-path "$HERE"
BIN="$(swift build -c release --package-path "$HERE" --show-bin-path)/$NAME"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$BIN" "$APP/Contents/MacOS/$NAME"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>$NAME</string>
    <key>CFBundleIdentifier</key><string>com.nvinnikov.claude-rc-app</string>
    <key>CFBundleName</key><string>ClaudeRC</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP"
echo "$APP"
