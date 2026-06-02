#!/bin/bash
# Build "Ask Widget.app" and install it to /Applications.
#   ./launcher/build-app.sh          # build + install
#   ./launcher/build-app.sh --no-install
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Ask Widget"
BIN_NAME="AskWidget"
BUILD="$DIR/build"
BUNDLE="$BUILD/$APP_NAME.app"

echo "→ Cleaning…"
rm -rf "$BUILD"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"

echo "→ Drawing icon…"
swiftc -O "$DIR/make-icon.swift" -o "$BUILD/make-icon"
"$BUILD/make-icon" "$BUILD/icon.png"
ICONSET="$BUILD/AppIcon.iconset"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s        "$BUILD/icon.png" --out "$ICONSET/icon_${s}x${s}.png"     >/dev/null
  sips -z $((s*2)) $((s*2)) "$BUILD/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUNDLE/Contents/Resources/AppIcon.icns"

echo "→ Compiling Swift…"
swiftc -framework Cocoa -framework WebKit -framework UniformTypeIdentifiers -O \
  "$DIR/AskWidget.swift" -o "$BUNDLE/Contents/MacOS/$BIN_NAME"

echo "→ Assembling bundle…"
cp "$DIR/Info.plist" "$BUNDLE/Contents/Info.plist"
# Ad-hoc sign so Gatekeeper/TCC treat it as a stable app (no Developer ID needed
# for a personal/local app; this avoids the "damaged"/unidentified prompts).
codesign --force --deep --sign - "$BUNDLE" 2>/dev/null || echo "  (codesign skipped)"

if [ "${1:-}" = "--no-install" ]; then
  echo "✓ Built: $BUNDLE"
  exit 0
fi

DEST="/Applications/$APP_NAME.app"
echo "→ Installing to ${DEST}"
rm -rf "$DEST"
cp -R "$BUNDLE" "$DEST"
# Clear the quarantine flag so it opens without the unidentified-developer prompt.
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

echo ""
echo "✓ Installed: $DEST"
echo "  Open it from Spotlight/Launchpad as 'Ask Widget', or:  open -a 'Ask Widget'"
