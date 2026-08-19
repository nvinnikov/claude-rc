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

# Иконку собираем из одного PNG: iconutil хочет каталог .iconset с набором
# размеров, включая @2x. Держать в git одиннадцать файлов вместо одного незачем.
mkdir -p "$APP/Contents/Resources"
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
for SIZE in 16 32 128 256 512; do
    sips -z $SIZE $SIZE "$HERE/icon.png" --out "$ICONSET/icon_${SIZE}x${SIZE}.png" >/dev/null
    sips -z $((SIZE * 2)) $((SIZE * 2)) "$HERE/icon.png" \
        --out "$ICONSET/icon_${SIZE}x${SIZE}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$(dirname "$ICONSET")"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>$NAME</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
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
